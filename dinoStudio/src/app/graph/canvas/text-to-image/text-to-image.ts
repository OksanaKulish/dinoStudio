import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

@Component({
	selector: 'app-text-to-image',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './text-to-image.html',
	styleUrl: './text-to-image.scss',
})
export class TextToImageComponent implements OnInit, OnDestroy {
	protected readonly prompt = signal('');
	protected readonly generating = signal(false);
	protected readonly progress = signal<number | null>(null);
	protected readonly phase = signal('Loading model');
	protected readonly imageUrl = signal<string | null>(null);
	protected readonly device = signal('Detecting...');
	protected readonly error = signal('');
	private progressTimer: ReturnType<typeof setInterval> | null = null;

	public ngOnInit(): void {
		void this.detectDevice();
	}

	private async detectDevice(): Promise<void> {
		try {
			const response = await fetchLocalApi('/api/image/device', {});
			const result: { device?: string } = await response.json();
			this.device.set(response.ok ? result.device ?? 'Unavailable' : 'Unavailable');
		} catch {
			this.device.set('Unavailable');
		}
	}

	public ngOnDestroy(): void {
		if (this.progressTimer) clearInterval(this.progressTimer);
	}

	private async refreshProgress(): Promise<void> {
		try {
			const response = await fetchLocalApi('/api/image/progress', {});
			if (!response.ok || !this.generating()) return;
			const status: { phase: string; progress: number | null } = await response.json();
			if (status.phase === 'generating') {
				this.phase.set('Generating image');
				this.progress.set(status.progress);
			}
		} catch {
			// The generation request reports any service error.
		}
	}

	protected async generate(): Promise<void> {
		const prompt = this.prompt().trim();
		if (this.generating() || !prompt) return;
		this.generating.set(true);
		this.phase.set('Loading model');
		this.progress.set(null);
		this.error.set('');
		this.progressTimer = setInterval(() => { void this.refreshProgress(); }, 750);
		try {
			const response = await fetchLocalApi('/api/image/generate', { prompt });
			const result: { image?: string; device?: string; error?: string } = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Image generation failed.');
			if (!result.image) throw new Error('Image generator returned no image.');
			this.imageUrl.set(`data:image/png;base64,${result.image}`);
			this.device.set(result.device ?? '');
		} catch (error) {
			this.error.set(error instanceof Error ? error.message : 'Image generation failed.');
		} finally {
			if (this.progressTimer) clearInterval(this.progressTimer);
			this.progressTimer = null;
			this.generating.set(false);
		}
	}
}