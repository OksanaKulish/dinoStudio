import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Subscription, switchMap, throwError, timer } from 'rxjs';

import { JobStatus } from '../../scraper/data-access/scraper-api.service';
import { DatasetSummary, EvaluationExample, EvaluationReport, EvaluationScore, RegisteredModel, TrainingApiService, TrainingConfig, TrainingLog, TrainingPreflight, TrainingRun } from '../data-access/training-api.service';

type NumberField = 'max_seq_length' | 'lora_r' | 'lora_alpha' | 'epochs' | 'learning_rate' | 'batch_size' | 'grad_accum' | 'seed';
type SummaryRow = { task_type: string; train: number; test: number; too_long: number };
type ComparisonRow = { task_type: string; metric: string; base: number | null; fine_tuned: number | null };

@Component({
	selector: 'app-training-page',
	standalone: true,
	imports: [MatButtonModule, MatButtonToggleModule, MatIconModule, MatProgressBarModule, MatTableModule, MatTabsModule, MatTooltipModule],
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './training-page.html',
	styleUrl: './training-page.scss',
})
export class TrainingPageComponent {
	private readonly api = inject(TrainingApiService);
	private readonly destroyRef = inject(DestroyRef);
	private runPoll: Subscription | null = null;
	private logStream: Subscription | null = null;
	private attemptedEvaluationId: string | null = null;
	private previewVersion = 0;
	private preflightVersion = 0;

	protected readonly activeTab = signal(0);
	protected readonly config = signal<TrainingConfig | null>(null);
	protected readonly jobs = signal<JobStatus[]>([]);
	protected readonly runs = signal<TrainingRun[]>([]);
	protected readonly run = signal<TrainingRun | null>(null);
	protected readonly selectedRunId = signal<string | null>(null);
	protected readonly logs = signal<TrainingLog[]>([]);
	protected readonly activeModel = signal<RegisteredModel | null>(null);
	protected readonly actionBusy = signal(false);
	protected readonly evaluation = signal<EvaluationReport | null>(null);
	protected readonly evaluationBusy = signal(false);
	protected readonly selectedExampleId = signal('');
	protected readonly promptValue = signal('');
	protected readonly maxTokens = signal(1500);
	protected readonly generating = signal(false);
	protected readonly generatedText = signal<string | null>(null);
	protected readonly sourceMode = signal<'job' | 'zip'>('job');
	protected readonly selectedJobId = signal('');
	protected readonly selectedZip = signal<File | null>(null);
	protected readonly preflight = signal<TrainingPreflight | null>(null);
	protected readonly preview = signal<DatasetSummary | null>(null);
	protected readonly preflightBusy = signal(false);
	protected readonly previewBusy = signal(false);
	protected readonly starting = signal(false);
	protected readonly error = signal('');
	protected readonly notice = signal('');
	protected readonly completedJobs = computed(() => this.jobs().filter(job => job.status === 'completed'));
	protected readonly summaryRows = computed<SummaryRow[]>(() => {
		const preview = this.preview();
		if (!preview) return [];
		const taskTypes = new Set([...Object.keys(preview.counts_per_task), ...preview.too_long_rows.map(row => row.task_type)]);
		return Array.from(taskTypes).sort().map(task_type => ({
			task_type, ...(preview.counts_per_task[task_type] ?? { train: 0, test: 0 }),
			too_long: preview.too_long_rows.filter(row => row.task_type === task_type).length,
		}));
	});
	protected readonly summaryColumns = ['task_type', 'train', 'test', 'too_long'];
	protected readonly runColumns = ['created_at', 'status', 'base_model', 'train_examples', 'final_loss', 'eval_score', 'actions'];
	protected readonly comparisonColumns = ['task_type', 'metric', 'base', 'fine_tuned'];
	protected readonly evaluatedRuns = computed(() => this.runs().filter(run => run.eval_status === 'completed'));
	protected readonly comparisonRows = computed<ComparisonRow[]>(() => {
		const report = this.evaluation();
		if (!report) return [];
		const metrics: { key: keyof EvaluationScore; label: string }[] = [
			{ key: 'quality_score', label: 'Quality score' },
			{ key: 'parse_success_rate', label: 'Parse success' },
			{ key: 'endpoint_coverage', label: 'Endpoint coverage' },
			{ key: 'consistency_errors', label: 'Consistency errors' },
			{ key: 'dto_exact_match_rate', label: 'DTO exact match' },
			{ key: 'dto_similarity', label: 'DTO similarity' },
		];
		return Object.entries({ Overall: report.overall, ...report.by_task_type }).flatMap(([task_type, pair]) =>
			metrics.map(({ key, label }) => ({ task_type, metric: label,
				base: pair.base[key], fine_tuned: pair.fine_tuned[key] })));
	});
	protected readonly selectedExample = computed<EvaluationExample | null>(() =>
		this.evaluation()?.examples.find(example => example.id === this.selectedExampleId()) ?? null);
	protected readonly lossSeries = computed(() => this.logs()
		.filter(event => event.source === 'progress' && event.stage === 'training' &&
			typeof event.step === 'number' && typeof event.loss === 'number')
		.map(event => ({ step: event.step as number, loss: event.loss as number })));
	protected readonly lossLine = computed(() => {
		const points = this.lossSeries();
		if (!points.length) return '';
		const maxStep = Math.max(2, ...points.map(point => point.step));
		const losses = points.map(point => point.loss);
		const low = Math.min(...losses);
		const span = Math.max(0.1, Math.max(...losses) - low);
		return points.map(point => `${24 + point.step / maxStep * 592},${138 - (point.loss - low) / span * 112}`).join(' ');
	});
	protected readonly runProgress = computed(() => {
		const run = this.run();
		return run?.total_steps ? Math.min(100, Math.round(run.step / run.total_steps * 100)) : 0;
	});
	protected readonly evaluationProgress = computed(() => {
		const run = this.run();
		return run?.eval_total_steps ? Math.min(100, Math.round(run.eval_step / run.eval_total_steps * 100)) : 0;
	});

	public constructor() {
		this.api.getConfig().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: config => { this.config.set(config); this.checkPreflight(); },
			error: error => this.error.set(this.errorMessage(error)),
		});
		this.refreshJobs();
		this.refreshRuns();
		this.refreshActiveModel();
	}

	protected refreshJobs(): void {
		this.api.listScraperJobs().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: jobs => this.jobs.set(jobs),
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected refreshRuns(): void {
		this.api.listRuns().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: runs => this.runs.set(runs),
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected refreshActiveModel(): void {
		this.api.activeModel().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: model => this.activeModel.set(model),
			error: error => {
			if (error instanceof HttpErrorResponse && error.status === 404) this.activeModel.set(null);
			else this.error.set(this.errorMessage(error));
		},
		});
	}

	protected chooseSource(mode: 'job' | 'zip'): void {
		this.sourceMode.set(mode);
		this.invalidatePreview();
		this.error.set('');
	}

	protected chooseZip(event: Event): void {
		const input = event.target as HTMLInputElement;
		this.selectedZip.set(input.files?.[0] ?? null);
		this.invalidatePreview();
		this.error.set('');
	}

	protected selectJob(value: string): void {
		this.selectedJobId.set(value);
		this.invalidatePreview();
	}

	protected updateText(field: 'base_model' | 'dry_run_model', value: string): void {
		this.config.update(config => config ? { ...config, [field]: value.trim() } : config);
		this.configurationChanged();
	}

	protected updateNumber(field: NumberField, value: string): void {
		const number = Number(value);
		if (!Number.isFinite(number)) return;
		this.config.update(config => config ? { ...config, [field]: number } : config);
		this.configurationChanged();
	}

	protected updateTaskTypes(value: string): void {
		this.config.update(config => config ? { ...config,
			task_types: value.split(',').map(task => task.trim()).filter(Boolean),
		} : config);
		this.configurationChanged();
	}

	protected updateQuantization(value: string): void {
		if (!['q4_k_m', 'q5_k_m', 'q8_0'].includes(value)) return;
		this.config.update(config => config ? { ...config, quantization: value as TrainingConfig['quantization'] } : config);
		this.configurationChanged();
	}

	protected setDryRun(checked: boolean): void {
		this.config.update(config => config ? { ...config, dry_run: checked } : config);
		this.configurationChanged();
	}

	private configurationChanged(): void {
		this.invalidatePreview();
		this.preflight.set(null);
		this.checkPreflight();
	}

	private invalidatePreview(): void {
		this.previewVersion++;
		this.preview.set(null);
		this.previewBusy.set(false);
	}

	protected checkPreflight(): void {
		const config = this.config();
		if (!config) return;
		this.preflightBusy.set(true);
		const version = ++this.preflightVersion;
		const model = config.dry_run ? config.dry_run_model : config.base_model;
		this.api.preflight(model).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: report => {
				if (version !== this.preflightVersion) return;
				this.preflight.set(report);
				this.preflightBusy.set(false);
			},
			error: error => {
				if (version !== this.preflightVersion) return;
				this.error.set(this.errorMessage(error));
				this.preflightBusy.set(false);
			},
		});
	}

	protected previewDataset(): void {
		const config = this.config();
		if (!config || !this.hasSource() || this.previewBusy()) return;
		const version = ++this.previewVersion;
		this.previewBusy.set(true);
		this.error.set('');
		this.preview.set(null);
		this.api.preview(config, this.sourceMode() === 'job' ? this.selectedJobId() : undefined,
			this.sourceMode() === 'zip' ? this.selectedZip() ?? undefined : undefined)
			.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
				next: preview => {
					if (version !== this.previewVersion) return;
					this.preview.set(preview);
					this.previewBusy.set(false);
				},
				error: error => {
					if (version !== this.previewVersion) return;
					this.error.set(this.errorMessage(error));
					this.previewBusy.set(false);
				},
			});
	}

	protected hasSource(): boolean {
		return this.sourceMode() === 'job' ? !!this.selectedJobId() : !!this.selectedZip();
	}

	protected start(): void {
		const config = this.config();
		if (!config || !this.preview() || !this.hasSource() || this.starting()) return;
		this.starting.set(true);
		this.error.set('');
		const model = config.dry_run ? config.dry_run_model : config.base_model;
		this.api.preflight(model).pipe(
			switchMap(report => {
				if (this.config() !== config || !this.preview() || !this.hasSource()) {
					return throwError(() => new Error('Source or configuration changed; preview the dataset again.'));
				}
				this.preflight.set(report);
				return report.ready ? this.api.startRun(config,
					this.sourceMode() === 'job' ? this.selectedJobId() : undefined,
					this.sourceMode() === 'zip' ? this.selectedZip() ?? undefined : undefined)
					: throwError(() => new Error(report.reasons.join(' ')));
			}),
			takeUntilDestroyed(this.destroyRef),
		).subscribe({
			next: run => {
				this.starting.set(false);
				this.notice.set(`Run ${run.run_id} started.`);
				this.refreshRuns();
				this.selectRun(run.run_id);
			},
			error: error => { this.error.set(this.errorMessage(error)); this.starting.set(false); },
		});
	}

	protected selectRun(runId: string): void {
		this.runPoll?.unsubscribe();
		this.selectedRunId.set(runId);
		this.run.set(this.runs().find(item => item.run_id === runId) ?? null);
		this.logs.set([]);
		this.evaluation.set(null);
		this.selectedExampleId.set('');
		this.attemptedEvaluationId = null;
		this.activeTab.set(2);
		this.runPoll = timer(0, 2000).pipe(
			switchMap(() => this.api.getRun(runId)),
			takeUntilDestroyed(this.destroyRef),
		).subscribe({
			next: run => {
				this.run.set(run);
				this.runs.update(runs => runs.map(item => item.run_id === runId ? run : item));
				if (run.eval_status === 'completed' && !this.evaluation() && this.attemptedEvaluationId !== runId) {
					this.loadEvaluation(runId);
				}
			},
			error: error => this.error.set(this.errorMessage(error)),
		});
		this.connectLogs(runId);
	}

	private connectLogs(runId: string): void {
		this.logStream?.unsubscribe();
		this.logStream = this.api.streamLogs(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: event => this.logs.update(logs => [...logs, event].slice(-400)),
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected evaluateRun(runId: string): void {
		if (this.actionBusy()) return;
		this.actionBusy.set(true);
		this.api.evaluateRun(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: run => {
				this.run.set(run);
				this.actionBusy.set(false);
				this.evaluation.set(null);
				this.attemptedEvaluationId = null;
				this.connectLogs(runId);
			},
			error: error => { this.error.set(this.errorMessage(error)); this.actionBusy.set(false); },
		});
	}

	protected openEvaluation(runId: string): void {
		if (!runId) return;
		if (this.run()?.run_id !== runId) this.selectRun(runId);
		this.activeTab.set(3);
		this.loadEvaluation(runId);
	}

	protected loadEvaluation(runId: string): void {
		if (this.evaluationBusy()) return;
		this.evaluationBusy.set(true);
		this.attemptedEvaluationId = runId;
		this.api.getEvaluation(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: report => {
				if (this.selectedRunId() === runId) {
					this.evaluation.set(report);
					this.selectedExampleId.set(report.examples[0]?.id ?? '');
				}
				this.evaluationBusy.set(false);
			},
			error: error => { this.error.set(this.errorMessage(error)); this.evaluationBusy.set(false); },
		});
	}

	protected updateMaxTokens(value: string): void {
		const count = Number(value);
		if (Number.isInteger(count) && count >= 1 && count <= 4096) this.maxTokens.set(count);
	}

	protected generate(): void {
		const prompt = this.promptValue().trim();
		if (!this.activeModel() || !prompt || this.generating()) return;
		this.generating.set(true);
		this.error.set('');
		this.generatedText.set(null);
		this.api.generate(prompt, this.maxTokens()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: response => { this.generatedText.set(response.content); this.generating.set(false); },
			error: error => { this.error.set(this.errorMessage(error)); this.generating.set(false); },
		});
	}

	protected cancelRun(runId: string): void {
		if (this.actionBusy()) return;
		this.actionBusy.set(true);
		this.api.cancelRun(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: run => { this.run.set(run); this.actionBusy.set(false); this.refreshRuns(); },
			error: error => { this.error.set(this.errorMessage(error)); this.actionBusy.set(false); },
		});
	}

	protected deleteRun(runId: string): void {
		if (this.actionBusy() || !window.confirm('Delete this run and all its artifacts?')) return;
		this.actionBusy.set(true);
		this.api.deleteRun(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: () => {
				this.actionBusy.set(false);
				if (this.run()?.run_id === runId) {
					this.runPoll?.unsubscribe();
					this.logStream?.unsubscribe();
					this.run.set(null);
					this.selectedRunId.set(null);
				}
				this.refreshRuns();
				this.refreshActiveModel();
			},
			error: error => { this.error.set(this.errorMessage(error)); this.actionBusy.set(false); },
		});
	}

	protected downloadRun(runId: string): void {
		this.api.download(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: blob => {
				const url = URL.createObjectURL(blob);
				const anchor = document.createElement('a');
				anchor.href = url;
				anchor.download = `trained-model-${runId}.zip`;
				anchor.click();
				window.setTimeout(() => URL.revokeObjectURL(url), 1000);
			},
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected activateRun(runId: string): void {
		this.api.activate(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: model => { this.activeModel.set(model); this.notice.set(`Active model: ${model.base_model}`); },
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected formatTime(value: string): string {
		return new Date(value).toLocaleString();
	}

	protected formatLog(event: TrainingLog): string {
		const parts = [event.stage ?? event.message ?? event.source];
		if (typeof event.step === 'number') parts.push(`${event.step}/${event.total_steps ?? '—'}`);
		if (typeof event.loss === 'number') parts.push(`loss ${event.loss}`);
		if (typeof event.eta_seconds === 'number') parts.push(`ETA ${event.eta_seconds}s`);
		if (event.error) parts.push(event.error);
		return parts.join(' · ');
	}

	protected formatMetric(value: number | null): string {
		return value === null ? '—' : Number.isInteger(value) ? String(value) : value.toFixed(3);
	}

	private errorMessage(error: unknown): string {
		if (error instanceof HttpErrorResponse) {
			const detail = error.error?.detail;
			if (Array.isArray(detail)) {
				return detail.map(item => `${item.loc?.join('.') ?? 'config'}: ${item.msg ?? 'invalid value'}`).join('; ');
			}
			return typeof detail === 'string' ? detail : error.message;
		}
		return error instanceof Error ? error.message : 'Training request failed.';
	}
}