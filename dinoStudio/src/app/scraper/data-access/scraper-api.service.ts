import { HttpClient, HttpEvent, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { EMPTY, Observable, catchError, concat, filter, map, merge, of, shareReplay, switchMap, take, tap, throwError, throwIfEmpty, timeout } from 'rxjs';

export interface ScraperConfig {
	ignore_dirs: string[];
	ignore_file_patterns: string[];
	allowed_extensions: string[];
	max_file_bytes: number;
	test_features: string[];
	max_tokens: number;
	tokenizer_model: string;
	mutation_kinds: string[];
	include_path_hint: boolean;
	use_git: boolean;
	system_msg: string;
	max_mutations: number;
}

export interface ProjectResponse {
	project_id: string;
	name: string;
}

export interface UploadStatus {
	upload_id: string;
	stage: string;
	processed_files: number;
	total_files: number;
	error: string | null;
	events: { id: number; time: string; message: string }[];
}

export interface JobStatus {
	job_id: string;
	project_id: string;
	project_name?: string | null;
	status: 'queued' | 'running' | 'completed' | 'failed';
	stage: string;
	percent: number;
	results_ready: boolean;
	events: { id: number; time: string; message: string }[];
	error: string | null;
	created_at: string;
	updated_at: string;
}

export interface SectionPage {
	section: string;
	items: Record<string, unknown>[];
	total: number;
	offset: number;
	limit: number;
}

interface ScraperHealth {
	service: string;
	version: string;
}

@Injectable({ providedIn: 'root' })
export class ScraperApiService {
	private readonly http = inject(HttpClient);
	private readonly apiUrl$: Observable<string> = this.discoverApiUrl();

	private discoverApiUrl(): Observable<string> {
		const configuredUrl = (globalThis as typeof globalThis & { SCRAPER_API_URL?: string }).SCRAPER_API_URL;
		if (configuredUrl) return of(configuredUrl.replace(/\/$/, ''));

		const probes = Array.from({ length: 21 }, (_, index) => {
			const url = `http://127.0.0.1:${8000 + index}`;
			return this.http.get<ScraperHealth>(`${url}/api/health`).pipe(
				filter(health => health.service === 'scraper-api'),
				map(() => url),
				catchError(() => EMPTY),
			);
		});

		const usualPorts = merge(probes[0], probes[1]).pipe(
			take(1),
			timeout({ first: 1500 }),
			catchError(() => EMPTY),
		);
		return concat(usualPorts, merge(...probes)).pipe(
			take(1),
			throwIfEmpty(),
			timeout({ first: 5000 }),
			shareReplay({ bufferSize: 1, refCount: false }),
			catchError(() => throwError(() => new Error(
				`No scraper API responded on 127.0.0.1 ports 8000-8020 within 5 seconds for ${globalThis.location.origin}. ` +
				'Start `python -m scraper_api.run` and check its CORS origin.',
			))),
		);
	}

	apiBaseUrl(): Observable<string> {
		return this.apiUrl$;
	}

	getDefaultConfig(): Observable<ScraperConfig> {
		return this.apiUrl$.pipe(switchMap(url => this.http.get<ScraperConfig>(`${url}/api/config/default`)));
	}

	uploadZip(name: string, archive: Blob, uploadId: string, onApiReady?: (url: string) => void): Observable<HttpEvent<ProjectResponse>> {
		const form = new FormData();
		form.append('file', archive, `${name || 'project'}.zip`);
		const params = new HttpParams().set('upload_id', uploadId);
		return this.apiUrl$.pipe(tap(url => onApiReady?.(url)), switchMap(url => this.http.post<ProjectResponse>(`${url}/api/projects`, form, {
			observe: 'events', reportProgress: true, params,
		}).pipe(timeout({ each: 120_000 }))));
	}

	getUploadStatus(uploadId: string): Observable<UploadStatus> {
		return this.apiUrl$.pipe(switchMap(url => this.http.get<UploadStatus>(`${url}/api/uploads/${encodeURIComponent(uploadId)}`)));
	}

	registerLocalPath(path: string): Observable<ProjectResponse> {
		return this.apiUrl$.pipe(switchMap(url => this.http.post<ProjectResponse>(`${url}/api/projects/local`, { path })));
	}

	startJob(projectId: string, config: ScraperConfig): Observable<{ job_id: string }> {
		return this.apiUrl$.pipe(switchMap(url => this.http.post<{ job_id: string }>(`${url}/api/jobs`, { project_id: projectId, config })));
	}

	getJob(jobId: string): Observable<JobStatus> {
		return this.apiUrl$.pipe(switchMap(url => this.http.get<JobStatus>(`${url}/api/jobs/${encodeURIComponent(jobId)}`)));
	}

	getSection(jobId: string, section: string, offset: number, limit = 100, search = '', sortBy = '', descending = false): Observable<SectionPage> {
		let params = new HttpParams().set('offset', offset).set('limit', limit);
		if (search) params = params.set('search', search);
		if (sortBy) params = params.set('sort_by', sortBy).set('descending', descending);
		return this.apiUrl$.pipe(switchMap(url => this.http.get<SectionPage>(
			`${url}/api/jobs/${encodeURIComponent(jobId)}/${encodeURIComponent(section)}`,
			{ params },
		)));
	}

	download(jobId: string): Observable<Blob> {
		return this.apiUrl$.pipe(switchMap(url => this.http.get(
			`${url}/api/jobs/${encodeURIComponent(jobId)}/download`, { responseType: 'blob' },
		)));
	}
}