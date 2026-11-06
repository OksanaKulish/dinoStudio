import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSortModule, Sort, SortDirection } from '@angular/material/sort';
import { MatTableModule } from '@angular/material/table';
import { MatTabsModule } from '@angular/material/tabs';
import { MatTooltipModule } from '@angular/material/tooltip';
import { EMPTY, Subscription, TimeoutError, catchError, finalize, switchMap, takeWhile, timer } from 'rxjs';

import { FolderZipper, ScanProgress, ZippedProject } from '../data-access/folder-zipper';
import { JobStatus, ScraperApiService, ScraperConfig, SectionPage, UploadStatus } from '../data-access/scraper-api.service';

const SECTIONS = [
	{ key: 'files', label: 'Files', description: 'Scanned files, their sizes and types, Git activity, and whether they produced analysis items.' },
	{ key: 'endpoints', label: 'Endpoints', description: 'C# controller actions with HTTP verbs, routes, request parameters, response types, and authorization.' },
	{ key: 'calls', label: 'Calls', description: 'HTTP requests made by Angular services, including methods, routes, payloads, and response types.' },
	{ key: 'dto_pairs', label: 'DTO pairs', description: 'C# DTOs paired with TypeScript models by name or matching fields.' },
	{ key: 'matches', label: 'Matches', description: 'Angular HTTP calls matched to C# endpoints by HTTP verb and route, with match confidence.' },
	{ key: 'issues', label: 'Issues', description: 'Detected mismatches and validation findings, with severity, rule, and details.' },
	{ key: 'examples', label: 'Examples', description: 'Generated prompt-and-response examples, task types, token counts, and train/test splits.' },
	{ key: 'coverage', label: 'Coverage', description: 'C# and TypeScript files that produced no parsed analysis items.' },
] as const;

type SectionKey = typeof SECTIONS[number]['key'];
type ListConfigKey = 'ignore_dirs' | 'ignore_file_patterns' | 'allowed_extensions' | 'test_features' | 'mutation_kinds';
type NumberConfigKey = 'max_file_bytes' | 'max_tokens' | 'max_mutations';
type ActivityEntry = { time: string; message: string };
type SelectedSource = { path: string; kind: 'folder' | 'files' | 'path' };

const TABLE_COLUMNS: Record<SectionKey, string[]> = {
	files: ['file_path', 'extension', 'kind', 'size_bytes', 'n_commits', 'n_authors', 'last_commit_date', 'last_commit_msg', 'produced_items'],
	endpoints: ['id', 'controller', 'action', 'verb', 'route', 'body_type', 'query_params', 'response_type', 'auth'],
	calls: ['service', 'method', 'verb', 'route', 'body_type', 'sent_keys', 'response_type'],
	dto_pairs: ['csharp', 'typescript', 'matched_by'],
	matches: ['call_i', 'ep_i', 'confidence'],
	issues: ['severity', 'rule', 'detail', 'controller', 'action', 'service', 'method'],
	examples: ['id', 'task_type', 'feature', 'source', 'split', 'n_tokens', 'too_long'],
	coverage: ['extension', 'file_path', 'produced_items'],
};

@Component({
	selector: 'app-scraper-page',
	standalone: true,
	imports: [MatButtonModule, MatIconModule, MatProgressBarModule, MatSortModule, MatTableModule, MatTabsModule, MatTooltipModule],
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './scraper-page.html',
	styleUrl: './scraper-page.scss',
})
export class ScraperPageComponent {
	private readonly api = inject(ScraperApiService);
	private readonly zipper = inject(FolderZipper);
	private readonly destroyRef = inject(DestroyRef);
	private pollSubscription: Subscription | null = null;
	private uploadPollSubscription: Subscription | null = null;
	private sectionSubscription: Subscription | null = null;
	private searchSubscription: Subscription | null = null;
	private lastZipMilestone = 0;
	private lastScanLoggedFiles = 0;
	private lastScanLoggedAt = 0;
	private lastJobEventId = 0;
	private lastUploadEventId = 0;

	protected readonly sections = SECTIONS;
	protected readonly config = signal<ScraperConfig | null>(null);
	protected readonly projectId = signal<string | null>(null);
	protected readonly projectName = signal('');
	protected readonly selectedSource = signal<SelectedSource | null>(null);
	protected readonly localPathValue = signal('');
	protected readonly jobStatus = signal<JobStatus | null>(null);
	protected readonly pages = signal<Partial<Record<SectionKey, SectionPage>>>({});
	protected readonly activeSection = signal<SectionKey>('files');
	protected readonly activeTabIndex = signal(0);
	protected readonly searchQuery = signal('');
	protected readonly sortColumn = signal('');
	protected readonly sortDirection = signal<SortDirection>('');
	protected readonly loading = signal(false);
	protected readonly preparingProject = signal(false);
	protected readonly preparationStage = signal('Preparing project');
	protected readonly preparationPercent = signal<number | null>(null);
	protected readonly scanProgress = signal<ScanProgress | null>(null);
	protected readonly loadingSection = signal<SectionKey | null>(null);
	protected readonly error = signal('');
	protected readonly activity = signal<ActivityEntry[]>([]);
	protected readonly severityFilter = signal('all');
	protected readonly ruleFilter = signal('');
	protected readonly selectedExample = signal<Record<string, unknown> | null>(null);
	protected readonly activePage = computed(() => this.pages()[this.activeSection()] ?? null);
	protected readonly visibleRows = computed(() => {
		let rows = this.activePage()?.items ?? [];
		if (this.activeSection() === 'issues') {
			const severity = this.severityFilter();
			const rule = this.ruleFilter().trim().toLowerCase();
			rows = rows.filter(row =>
				(severity === 'all' || row['severity'] === severity) &&
				(!rule || String(row['rule'] ?? '').toLowerCase().includes(rule)));
		}
		return rows;
	});
	protected readonly emptyMessage = computed(() => {
		switch (this.activeSection()) {
			case 'endpoints':
				return 'No controller endpoints found. Include C# controller actions with [HttpGet], [HttpPost], or other HTTP verb attributes.';
			case 'dto_pairs':
				return 'No DTO pairs found. Include C# DTO classes and matching TypeScript models in the same project.';
			default:
				return 'No rows on this page.';
		}
	});

	public constructor() {
		this.api.getDefaultConfig().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: config => this.config.set(config),
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	protected chooseFolder(fallbackInput: HTMLInputElement): void {
		this.error.set('');
		fallbackInput.click();
	}

	protected async uploadFallback(event: Event): Promise<void> {
		const input = event.target as HTMLInputElement;
		if (!input.files?.length) return;
		const selectedFiles = Array.from(input.files);
		const relative = (selectedFiles[0] as File & { webkitRelativePath?: string }).webkitRelativePath;
		const folder = relative?.replace(/\\/g, '/').split('/')[0];
		this.selectedSource.set(folder
			? { path: folder, kind: 'folder' }
			: { path: selectedFiles.slice(0, 3).map(file => file.name).join(', '), kind: 'files' });
		this.error.set('');
		this.loading.set(true);
		this.preparingProject.set(true);
		this.preparationStage.set('Scanning selected files');
		this.preparationPercent.set(null);
		this.scanProgress.set({ files: 0, bytes: 0, path: relative || selectedFiles[0].name, processedFiles: 0, totalFiles: selectedFiles.length });
		this.lastZipMilestone = 0;
		this.lastScanLoggedFiles = 0;
		this.lastScanLoggedAt = 0;
		this.logActivity(`Scanning 0/${selectedFiles.length} selected files: ${selectedFiles.slice(0, 5).map(file => file.name).join(', ')}${selectedFiles.length > 5 ? ', ...' : ''}`);
		const startedAt = Date.now();
		const heartbeat = timer(10_000, 10_000).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => {
			const scan = this.scanProgress();
			const count = scan?.totalFiles === undefined ? '' : ` ${scan.processedFiles}/${scan.totalFiles} selected;`;
			this.logActivity(`Still ${this.preparationStage().toLowerCase()}:${count} current ${scan?.path ?? 'selected folder'} (${Math.round((Date.now() - startedAt) / 1000)}s)`);
		});
		try {
			const project = await this.zipper.zipFileList(
				input.files,
				percent => this.reportCompression(percent),
				progress => this.reportScan(progress),
			);
			this.uploadProject(project);
		} catch (error) {
			this.error.set(this.errorMessage(error));
			this.logActivity(`Folder preparation failed: ${this.error()}`);
			this.finishPreparation();
		} finally {
			heartbeat.unsubscribe();
			input.value = '';
		}
	}

	protected registerLocalPath(): void {
		const path = this.localPathValue().trim();
		if (!path.trim() || this.loading()) return;
		this.selectedSource.set({ path, kind: 'path' });
		this.loading.set(true);
		this.preparingProject.set(true);
		this.preparationStage.set('Registering local folder');
		this.preparationPercent.set(null);
		this.error.set('');
		this.logActivity('Registering local folder with the scraper API');
		this.api.registerLocalPath(path.trim()).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: project => {
				this.logActivity(`Registered ${project.name}`);
				this.setProject(project.project_id, project.name);
				this.finishPreparation();
			},
			error: error => {
				this.error.set(this.errorMessage(error));
				this.logActivity(`Registration failed: ${this.error()}`);
				this.finishPreparation();
			},
		});
	}

	private uploadProject(project: ZippedProject): void {
		this.loading.set(true);
		this.preparingProject.set(true);
		this.preparationStage.set('Locating scraper API');
		this.preparationPercent.set(null);
		this.error.set('');
		this.logActivity(`Created ZIP: ${project.fileCount} files, ${this.formatSize(project.uncompressedBytes)} uncompressed, ${this.formatSize(project.archive.size)} to upload`);
		this.logActivity('Checking local API on ports 8000-8020 (up to 5s); upload will start when it responds');
		const startedAt = Date.now();
		const heartbeat = timer(10_000, 10_000).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => {
			this.logActivity(`Still ${this.preparationStage().toLowerCase()} (${Math.round((Date.now() - startedAt) / 1000)}s elapsed)`);
		});
		let uploadSent = false;
		let apiReady = false;
		let finished = false;
		let uploadSubscription: Subscription | undefined;
		const lookupDeadline = window.setTimeout(() => {
			if (apiReady || !this.preparingProject()) return;
			finished = true;
			this.error.set('API discovery did not finish in this browser tab after 7 seconds. Check the API URL in this browser, then refresh and retry.');
			this.logActivity(`Upload failed: ${this.error()}`);
			this.finishPreparation();
			uploadSubscription?.unsubscribe();
		}, 7000);
		const uploadId = crypto.randomUUID();
		this.lastUploadEventId = 0;
		uploadSubscription = this.api.uploadZip(project.name, project.archive, uploadId, url => {
			apiReady = true;
			window.clearTimeout(lookupDeadline);
			this.logActivity(`Scraper API found at ${url}; starting ZIP upload`);
			this.uploadPollSubscription?.unsubscribe();
			this.uploadPollSubscription = timer(0, 1000).pipe(
				switchMap(() => this.api.getUploadStatus(uploadId).pipe(catchError(() => EMPTY))),
				takeUntilDestroyed(this.destroyRef),
			).subscribe(status => this.showUploadStatus(status));
		}).pipe(finalize(() => {
			window.clearTimeout(lookupDeadline);
			heartbeat.unsubscribe();
			this.uploadPollSubscription?.unsubscribe();
			if (!finished && this.preparingProject()) {
				this.error.set('Upload stopped before the API registered the project. Refresh and retry.');
				this.logActivity(`Upload failed: ${this.error()}`);
				this.finishPreparation();
			}
			if (apiReady) {
				this.api.getUploadStatus(uploadId).pipe(
					catchError(() => EMPTY), takeUntilDestroyed(this.destroyRef),
				).subscribe(status => this.showUploadStatus(status));
			}
		}), takeUntilDestroyed(this.destroyRef)).subscribe({
			next: event => {
				if (event.type === HttpEventType.Sent) {
					this.preparationStage.set('Uploading project ZIP');
					this.logActivity('Sending ZIP to scraper API');
				} else if (event.type === HttpEventType.UploadProgress && !uploadSent) {
					const total = event.total || project.archive.size;
					const percent = Math.min(100, Math.round(event.loaded / total * 100));
					const previous = this.preparationPercent() ?? 0;
					this.preparationPercent.set(percent);
					if (Math.floor(percent / 25) > Math.floor(previous / 25)) {
						this.logActivity(`Uploaded ${this.formatSize(event.loaded)} of ${this.formatSize(total)} (${percent}%)`);
					}
					if (percent === 100) {
						uploadSent = true;
						this.preparationStage.set('Validating and extracting ZIP');
						this.preparationPercent.set(null);
						this.logActivity('Upload sent; waiting for server to validate and extract files');
					}
				} else if (event.type === HttpEventType.ResponseHeader) {
					this.logActivity(`Scraper API responded with HTTP ${event.status}`);
				} else if (event.type === HttpEventType.Response) {
					finished = true;
					if (!event.body) {
						this.error.set('Scraper API returned an empty project response.');
						this.logActivity(`Upload failed: ${this.error()}`);
						this.finishPreparation();
						return;
					}
					this.logActivity(`Registered ${event.body.name}; ready to run analysis`);
					this.setProject(event.body.project_id, event.body.name);
					this.finishPreparation();
				}
			},
			error: error => {
				finished = true;
				this.error.set(this.errorMessage(error));
				this.logActivity(`Upload failed: ${this.error()}`);
				this.finishPreparation();
			},
		});
	}

	private showUploadStatus(status: UploadStatus): void {
		for (const event of status.events) {
			if (event.id <= this.lastUploadEventId) continue;
			this.logActivity(`API: ${event.message}`, new Date(event.time).toLocaleTimeString());
			this.lastUploadEventId = event.id;
		}
		if (this.preparingProject()) {
			this.preparationStage.set(`API: ${status.stage}${status.total_files ? ` (${status.processed_files}/${status.total_files})` : ''}`);
			this.preparationPercent.set(status.stage === 'Extracting files' && status.total_files
				? Math.round(status.processed_files / status.total_files * 100) : null);
		}
	}

	private reportCompression(percent: number): void {
		if (this.preparationStage() !== 'Compressing project ZIP') {
			this.logActivity(`Scanned ${this.scanProgress()?.files ?? 0} files; starting ZIP compression`);
		}
		this.preparationStage.set('Compressing project ZIP');
		this.preparationPercent.set(percent);
		const milestone = Math.floor(percent / 25);
		if (milestone > this.lastZipMilestone) {
			this.lastZipMilestone = milestone;
			this.logActivity(`Compressing ZIP: ${percent}%`);
		}
	}

	private reportScan(progress: ScanProgress): void {
		if (!this.scanProgress()) {
			this.preparationStage.set('Scanning folder');
			this.logActivity('Folder selected; scanning locally in browser. No ZIP has reached the API yet');
		}
		this.scanProgress.set(progress);
		const now = Date.now();
		const processed = progress.processedFiles ?? progress.files;
		const interval = progress.totalFiles !== undefined && progress.totalFiles <= 25 ? 4 : 25;
		if (processed - this.lastScanLoggedFiles >= interval ||
			(progress.totalFiles !== undefined && processed === progress.totalFiles && processed !== this.lastScanLoggedFiles) ||
			now - this.lastScanLoggedAt >= 5000) {
			const count = progress.totalFiles === undefined ? `${progress.files} files` :
				`${processed}/${progress.totalFiles} selected, ${progress.files} included`;
			this.logActivity(`Scanned ${count} (${this.formatSize(progress.bytes)}); current: ${progress.path}`);
			this.lastScanLoggedFiles = processed;
			this.lastScanLoggedAt = now;
		}
	}

	private formatSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
	}

	private logActivity(message: string, time = new Date().toLocaleTimeString()): void {
		this.activity.update(entries => [{ time, message }, ...entries].slice(0, 40));
	}

	protected updateList(key: ListConfigKey, value: string): void {
		this.config.update(config => config ? {
			...config,
			[key]: value.split(',').map(item => item.trim()).filter(Boolean),
		} : config);
	}

	protected updateNumber(key: NumberConfigKey, value: string): void {
		const parsed = Number(value);
		if (!Number.isFinite(parsed)) return;
		this.config.update(config => config ? { ...config, [key]: parsed } : config);
	}

	protected updateTokenizerModel(value: string): void {
		this.config.update(config => config ? { ...config, tokenizer_model: value } : config);
	}

	protected updateSystemMessage(value: string): void {
		this.config.update(config => config ? { ...config, system_msg: value } : config);
	}

	protected updateBoolean(key: 'include_path_hint' | 'use_git', checked: boolean): void {
		this.config.update(config => config ? { ...config, [key]: checked } : config);
	}

	protected startJob(): void {
		const projectId = this.projectId();
		const config = this.config();
		if (!projectId || !config || this.loading()) return;
		this.loading.set(true);
		this.preparingProject.set(false);
		this.error.set('');
		this.jobStatus.set(null);
		this.lastJobEventId = 0;
		this.resetTableControls();
		this.pages.set({});
		this.api.startJob(projectId, config).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: result => {
				this.loading.set(false);
				this.pollJob(result.job_id);
			},
			error: error => {
				this.error.set(this.errorMessage(error));
				this.loading.set(false);
			},
		});
	}

	private finishPreparation(): void {
		this.loading.set(false);
		this.preparingProject.set(false);
		this.preparationPercent.set(null);
	}

	protected selectTab(index: number): void {
		const section = this.sections[index];
		if (!section) return;
		this.resetTableControls();
		this.activeTabIndex.set(index);
		this.activeSection.set(section.key);
		this.selectedExample.set(null);
		if (this.jobStatus()?.results_ready) this.loadSection(section.key, 0);
	}

	protected updateSearch(value: string): void {
		this.searchQuery.set(value);
		this.selectedExample.set(null);
		this.searchSubscription?.unsubscribe();
		this.searchSubscription = timer(300).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.loadSection(this.activeSection(), 0));
	}

	protected changeSort(sort: Sort): void {
		this.sortColumn.set(sort.direction ? sort.active : '');
		this.sortDirection.set(sort.direction);
		this.selectedExample.set(null);
		this.loadSection(this.activeSection(), 0);
	}

	private resetTableControls(): void {
		this.searchSubscription?.unsubscribe();
		this.searchQuery.set('');
		this.sortColumn.set('');
		this.sortDirection.set('');
	}

	protected loadSection(section: SectionKey, offset: number): void {
		const jobId = this.jobStatus()?.job_id;
		if (!jobId) return;
		this.sectionSubscription?.unsubscribe();
		this.loadingSection.set(section);
		this.sectionSubscription = this.api.getSection(
			jobId, section, offset, 100, this.searchQuery(), this.sortColumn(), this.sortDirection() === 'desc',
		).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: page => {
				this.pages.update(pages => ({ ...pages, [section]: page }));
				this.loadingSection.set(null);
			},
			error: error => {
				this.error.set(this.errorMessage(error));
				this.loadingSection.set(null);
			},
		});
	}

	protected previousPage(): void {
		const page = this.activePage();
		if (page && page.offset > 0) this.loadSection(this.activeSection(), Math.max(0, page.offset - page.limit));
	}

	protected nextPage(): void {
		const page = this.activePage();
		if (page && page.offset + page.items.length < page.total) {
			this.loadSection(this.activeSection(), page.offset + page.limit);
		}
	}

	protected columns(): string[] {
		const defaults: Record<SectionKey, string[]> = {
			files: ['file_path', 'extension', 'kind', 'size_bytes', 'n_commits', 'n_authors', 'last_commit_date', 'last_commit_msg', 'produced_items'],
			endpoints: ['id', 'controller', 'action', 'verb', 'route', 'body_type', 'query_params', 'response_type', 'auth'],
			calls: ['service', 'method', 'verb', 'route', 'body_type', 'sent_keys', 'response_type'],
			dto_pairs: ['csharp', 'typescript', 'matched_by'],
			matches: ['call_i', 'ep_i', 'confidence'],
			issues: ['severity', 'rule', 'detail', 'controller', 'action', 'service', 'method'],
			examples: ['id', 'task_type', 'feature', 'source', 'split', 'n_tokens', 'too_long'],
			coverage: ['extension', 'file_path', 'produced_items'],
		};
		return defaults[this.activeSection()];
	}

	protected formatCell(value: unknown): string {
		if (value === null || value === undefined) return '';
		return typeof value === 'object' ? JSON.stringify(value) : String(value);
	}

	protected selectRow(row: Record<string, unknown>): void {
		if (this.activeSection() === 'examples') this.selectedExample.set(row);
	}

	protected download(): void {
		const jobId = this.jobStatus()?.job_id;
		if (!jobId) return;
		this.api.download(jobId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
			next: blob => {
				const url = URL.createObjectURL(blob);
				const anchor = document.createElement('a');
				anchor.href = url;
				anchor.download = `scraper-job-${jobId}.zip`;
				anchor.click();
				window.setTimeout(() => URL.revokeObjectURL(url), 1000);
			},
			error: error => this.error.set(this.errorMessage(error)),
		});
	}

	private setProject(projectId: string, name: string): void {
		this.pollSubscription?.unsubscribe();
		this.sectionSubscription?.unsubscribe();
		this.projectId.set(projectId);
		this.projectName.set(name);
		this.jobStatus.set(null);
		this.lastJobEventId = 0;
		this.resetTableControls();
		this.pages.set({});
		this.selectedExample.set(null);
		this.activeTabIndex.set(0);
		this.activeSection.set('files');
	}

	private pollJob(jobId: string): void {
		this.pollSubscription?.unsubscribe();
		const subscription = timer(0, 1000).pipe(
			switchMap(() => this.api.getJob(jobId)),
			takeWhile(status => status.status === 'queued' || status.status === 'running', true),
			takeUntilDestroyed(this.destroyRef),
		).subscribe({
			next: status => {
				const resultsBecameReady = status.results_ready && !this.jobStatus()?.results_ready;
				this.jobStatus.set(status);
				for (const event of status.events) {
					if (event.id <= this.lastJobEventId) continue;
					this.logActivity(`API: ${event.message}`, new Date(event.time).toLocaleTimeString());
					this.lastJobEventId = event.id;
				}
				if (resultsBecameReady || status.status === 'completed') this.loadSection(this.activeSection(), 0);
				if (status.status === 'failed') this.error.set(status.error ?? 'Scraper job failed.');
			},
			error: error => this.error.set(this.errorMessage(error)),
		});
		this.pollSubscription = subscription;
	}

	private errorMessage(error: unknown): string {
		if (error instanceof TimeoutError) {
			return `No upload activity or server response for 2 minutes during ${this.preparationStage().toLowerCase()}. Check the scraper API terminal and try again.`;
		}
		if (error instanceof HttpErrorResponse) {
			const detail = error.error?.detail;
			return typeof detail === 'string' ? detail : error.status === 0
				? 'Cannot reach the scraper API. Check that it is running and allows this app origin.'
				: error.message;
		}
		return error instanceof Error ? error.message : 'Scraper request failed.';
	}
}