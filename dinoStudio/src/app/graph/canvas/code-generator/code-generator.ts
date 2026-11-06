import { ChangeDetectionStrategy, Component, OnDestroy, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

interface CodeMessage {
	role: 'user' | 'assistant';
	content: string;
}

@Component({
	selector: 'app-code-generator',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './code-generator.html',
	styleUrl: './code-generator.scss',
})
export class CodeGeneratorComponent implements OnDestroy {
	protected readonly model = signal('Qwen/Qwen2.5-Coder-0.5B-Instruct');
	protected readonly language = signal('TypeScript');
	protected readonly prompt = signal('');
	protected readonly generatedCode = signal('');
	protected readonly device = signal('');
	protected readonly busy = signal(false);
	protected readonly progress = signal<number | null>(null);
	protected readonly progressPhase = signal('Loading Hugging Face model');
	protected readonly error = signal('');
	protected readonly copied = signal(false);
	private progressTimer: ReturnType<typeof setInterval> | null = null;

	public ngOnDestroy(): void {
		if (this.progressTimer) clearInterval(this.progressTimer);
	}

	private async refreshProgress(): Promise<void> {
		try {
			const response = await fetchLocalApi('/api/code/progress', {});
			if (!response.ok || !this.busy()) return;
			const status: { phase: string; progress: number | null } = await response.json();
			if (status.phase === 'generating') this.progressPhase.set('Generating code');
			this.progress.set(status.progress);
		} catch {
			// The generation request reports any service error.
		}
	}

	protected async generate(): Promise<void> {
		const prompt = this.prompt().trim();
		if (this.busy() || !this.model().trim() || !prompt) return;
		this.busy.set(true);
		this.progress.set(null);
		this.progressPhase.set('Loading Hugging Face model');
		this.error.set('');
		this.copied.set(false);
		this.progressTimer = setInterval(() => { void this.refreshProgress(); }, 750);
		try {
			const response = await fetchLocalApi('/api/code/generate', {
				model: this.model().trim(), language: this.language(), prompt,
			});
			const result: { code?: string; device?: string; error?: string } = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Code generation failed.');
			this.generatedCode.set(result.code ?? '');
			this.device.set(result.device ?? '');
		} catch (error) {
			this.error.set(error instanceof Error ? error.message : 'Code generation failed.');
		} finally {
			if (this.progressTimer) clearInterval(this.progressTimer);
			this.progressTimer = null;
			this.busy.set(false);
		}
	}

	protected async copyCode(): Promise<void> {
		if (!this.generatedCode()) return;
		try {
			await navigator.clipboard.writeText(this.generatedCode());
			this.copied.set(true);
		} catch {
			this.error.set('Clipboard access is unavailable in this browser context.');
		}
	}
}