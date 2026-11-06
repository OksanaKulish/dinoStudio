import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, switchMap } from 'rxjs';

import { JobStatus, ScraperApiService } from '../../scraper/data-access/scraper-api.service';

export interface TrainingConfig {
	base_model: string;
	dry_run_model: string;
	max_seq_length: number;
	lora_r: number;
	lora_alpha: number;
	epochs: number;
	learning_rate: number;
	batch_size: number;
	grad_accum: number;
	seed: number;
	quantization: 'q4_k_m' | 'q5_k_m' | 'q8_0';
	task_types: string[];
	dry_run: boolean;
}

export interface TrainingPreflight {
	ready: boolean;
	base_model: string;
	python: string;
	cuda: boolean;
	vram_total_gb: number | null;
	vram_free_gb: number | null;
	minimum_vram_gb: number;
	versions: Record<string, string | null>;
	reasons: string[];
}

export interface DatasetSummary {
	dataset_hash: string;
	counts_per_task: Record<string, { train: number; test: number }>;
	train_count: number;
	test_count: number;
	too_long_rows: { id: string; split: string; task_type: string; feature: string; tokens: number }[];
	warnings: string[];
	manifest: Record<string, unknown>;
}

export interface TrainingRun {
	run_id: string;
	job_id: string | null;
	status: string;
	stage: string;
	base_model: string;
	created_at: string;
	updated_at: string;
	step: number;
	total_steps: number | null;
	loss: number | null;
	eta_seconds: number | null;
	error: string | null;
	train_examples: number | null;
	test_examples: number | null;
	final_loss: number | null;
	gguf: string | null;
	eval_score: number | null;
	eval_status: string | null;
	eval_stage: string | null;
	eval_step: number;
	eval_total_steps: number | null;
	eval_error: string | null;
}

export interface EvaluationScore {
	examples: number;
	parse_success_rate: number | null;
	endpoint_coverage: number | null;
	consistency_errors: number | null;
	dto_exact_match_rate: number | null;
	dto_similarity: number | null;
	quality_score: number | null;
}

export interface EvaluationExample {
	id: string;
	task_type: string;
	feature: string;
	prompt: string;
	expected: string;
	base_output: string;
	fine_tuned_output: string;
	base_scores: Record<string, number | boolean | null>;
	fine_tuned_scores: Record<string, number | boolean | null>;
}

export interface EvaluationReport {
	base_model: string;
	dataset_hash: string;
	scoring: string;
	overall: { base: EvaluationScore; fine_tuned: EvaluationScore };
	by_task_type: Record<string, { base: EvaluationScore; fine_tuned: EvaluationScore }>;
	examples: EvaluationExample[];
}

export interface RegisteredModel {
	run_id: string;
	base_model: string;
	quantization: string;
	gguf: string;
	active: boolean;
}

export interface TrainingLog {
	source: 'progress' | 'log' | 'evaluation' | 'eval_log' | 'connection';
	time?: string;
	stage?: string;
	step?: number;
	total_steps?: number;
	loss?: number | null;
	eta_seconds?: number;
	error?: string;
	message?: string;
}

@Injectable({ providedIn: 'root' })
export class TrainingApiService {
	private readonly http = inject(HttpClient);
	private readonly scraper = inject(ScraperApiService);
	private readonly baseUrl$ = this.scraper.apiBaseUrl();

	getConfig(): Observable<TrainingConfig> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<TrainingConfig>(`${url}/api/training/config/default`)));
	}

	preflight(baseModel: string): Observable<TrainingPreflight> {
		const params = new HttpParams().set('base_model', baseModel);
		return this.baseUrl$.pipe(switchMap(url => this.http.get<TrainingPreflight>(`${url}/api/training/preflight`, { params })));
	}

	listScraperJobs(): Observable<JobStatus[]> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<JobStatus[]>(`${url}/api/jobs`)));
	}

	preview(config: TrainingConfig, jobId?: string, archive?: File): Observable<DatasetSummary> {
		return this.baseUrl$.pipe(switchMap(url => {
			if (archive) {
				const form = new FormData();
				form.append('file', archive);
				form.append('config', JSON.stringify(config));
				return this.http.post<DatasetSummary>(`${url}/api/training/preview`, form);
			}
			return this.http.post<DatasetSummary>(`${url}/api/training/preview`, { job_id: jobId, config });
		}));
	}

	startRun(config: TrainingConfig, jobId?: string, archive?: File): Observable<TrainingRun> {
		return this.baseUrl$.pipe(switchMap(url => {
			if (archive) {
				const form = new FormData();
				form.append('file', archive);
				form.append('config', JSON.stringify(config));
				return this.http.post<TrainingRun>(`${url}/api/training/runs`, form);
			}
			return this.http.post<TrainingRun>(`${url}/api/training/runs`, { job_id: jobId, config });
		}));
	}

	listRuns(): Observable<TrainingRun[]> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<TrainingRun[]>(`${url}/api/training/runs`)));
	}

	getRun(runId: string): Observable<TrainingRun> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<TrainingRun>(`${url}/api/training/runs/${encodeURIComponent(runId)}`)));
	}

	streamLogs(runId: string): Observable<TrainingLog> {
		return this.baseUrl$.pipe(switchMap(url => new Observable<TrainingLog>(observer => {
			const stream = new EventSource(`${url}/api/training/runs/${encodeURIComponent(runId)}/logs`);
			const sources = ['progress', 'log', 'evaluation', 'eval_log'] as const;
			const listeners = sources.map(source => {
				const listener = (event: Event) => {
					try {
						observer.next({ ...JSON.parse((event as MessageEvent).data), source });
					} catch {
						observer.next({ source: 'connection', message: 'A log entry could not be read.' });
					}
				};
				stream.addEventListener(source, listener);
				return { source, listener };
			});
			const onEnd = () => observer.complete();
			stream.addEventListener('end', onEnd);
			stream.onerror = () => observer.next({ source: 'connection', message: 'Log connection interrupted; reconnecting.' });
			return () => {
				for (const { source, listener } of listeners) stream.removeEventListener(source, listener);
				stream.removeEventListener('end', onEnd);
				stream.close();
			};
		})));
	}

	cancelRun(runId: string): Observable<TrainingRun> {
		return this.baseUrl$.pipe(switchMap(url => this.http.post<TrainingRun>(`${url}/api/training/runs/${encodeURIComponent(runId)}/cancel`, {})));
	}

	deleteRun(runId: string): Observable<void> {
		return this.baseUrl$.pipe(switchMap(url => this.http.delete<void>(`${url}/api/training/runs/${encodeURIComponent(runId)}`)));
	}

	evaluateRun(runId: string): Observable<TrainingRun> {
		return this.baseUrl$.pipe(switchMap(url => this.http.post<TrainingRun>(`${url}/api/training/runs/${encodeURIComponent(runId)}/evaluate`, {})));
	}

	getEvaluation(runId: string): Observable<EvaluationReport> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<EvaluationReport>(`${url}/api/training/runs/${encodeURIComponent(runId)}/eval`)));
	}

	download(runId: string): Observable<Blob> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get(`${url}/api/training/runs/${encodeURIComponent(runId)}/download`, { responseType: 'blob' })));
	}

	activate(runId: string): Observable<RegisteredModel> {
		return this.baseUrl$.pipe(switchMap(url => this.http.post<RegisteredModel>(`${url}/api/training/runs/${encodeURIComponent(runId)}/activate`, {})));
	}

	listModels(): Observable<RegisteredModel[]> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<RegisteredModel[]>(`${url}/api/training/models`)));
	}

	activeModel(): Observable<RegisteredModel> {
		return this.baseUrl$.pipe(switchMap(url => this.http.get<RegisteredModel>(`${url}/api/training/models/active`)));
	}

	generate(prompt: string, maxTokens: number): Observable<{ run_id: string; content: string; usage: Record<string, number> | null }> {
		return this.baseUrl$.pipe(switchMap(url => this.http.post<{ run_id: string; content: string; usage: Record<string, number> | null }>(
			`${url}/api/generate`, { prompt, max_tokens: maxTokens },
		)));
	}
}