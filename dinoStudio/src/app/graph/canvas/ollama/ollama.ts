import { ChangeDetectionStrategy, Component, OnInit, signal } from '@angular/core';
import { fetchLocalApi } from '../../../local-api';

interface ChatMessage {
	role: 'user' | 'assistant';
	content: string;
}

@Component({
	selector: 'app-ollama',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './ollama.html',
	styleUrl: './ollama.scss',
})
export class OllamaComponent implements OnInit {
	protected readonly models = signal<string[]>([]);
	protected readonly model = signal('');
	protected readonly prompt = signal('');
	protected readonly messages = signal<ChatMessage[]>([]);
	protected readonly busy = signal(false);
	protected readonly loadingModels = signal(false);
	protected readonly error = signal('');

	public ngOnInit(): void {
		void this.refreshModels();
	}

	protected async refreshModels(): Promise<void> {
		if (this.loadingModels()) return;
		this.loadingModels.set(true);
		this.error.set('');
		try {
			const response = await fetchLocalApi('/api/ollama/models', {});
			const result: { models?: string[]; error?: string } = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Could not list Ollama models.');
			this.models.set(result.models ?? []);
			if (!result.models?.includes(this.model())) this.model.set(result.models?.[0] ?? '');
			if (!result.models?.length) this.error.set('No models installed. Run ollama pull <model> first.');
		} catch (error) {
			this.error.set(error instanceof Error ? error.message : 'Ollama is unavailable.');
		} finally {
			this.loadingModels.set(false);
		}
	}

	protected selectModel(value: string): void {
		this.model.set(value);
		this.messages.set([]);
	}

	protected async send(): Promise<void> {
		const content = this.prompt().trim();
		if (this.busy() || !this.model() || !content) return;
		this.busy.set(true);
		this.error.set('');
		const history = this.messages();
		const messages: ChatMessage[] = [...history, { role: 'user', content }];
		this.messages.set(messages);
		try {
			const response = await fetchLocalApi('/api/ollama/chat', { model: this.model(), messages: messages.slice(-30) });
			const result: { message?: string; error?: string } = await response.json();
			if (!response.ok) throw new Error(result.error ?? 'Ollama request failed.');
			this.messages.set([...messages, { role: 'assistant', content: result.message ?? '' }]);
			this.prompt.set('');
		} catch (error) {
			this.messages.set(history);
			this.error.set(error instanceof Error ? error.message : 'Ollama request failed.');
		} finally {
			this.busy.set(false);
		}
	}
}