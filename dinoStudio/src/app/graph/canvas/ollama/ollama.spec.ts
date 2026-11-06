import { ComponentFixture, TestBed } from '@angular/core/testing';
import { fetchLocalApi } from '../../../local-api';
import { OllamaComponent } from './ollama';

vi.mock('../../../local-api', () => ({ fetchLocalApi: vi.fn() }));

describe('OllamaComponent', () => {
	let fixture: ComponentFixture<OllamaComponent>;

	beforeEach(async () => {
		vi.mocked(fetchLocalApi).mockResolvedValueOnce({
			ok: true, json: async () => ({ models: ['llama3.2:latest'] }),
		} as Response);
		await TestBed.configureTestingModule({ imports: [OllamaComponent] }).compileComponents();
		fixture = TestBed.createComponent(OllamaComponent);
		fixture.detectChanges();
		await fixture.whenStable();
		fixture.detectChanges();
	});

	afterEach(() => vi.resetAllMocks());

	it('lists models and shows a response to a prompt', async () => {
		vi.mocked(fetchLocalApi).mockResolvedValueOnce({
			ok: true, json: async () => ({ message: 'Hello from Ollama' }),
		} as Response);
		const root: HTMLElement = fixture.nativeElement;
		const prompt = root.querySelector<HTMLTextAreaElement>('#ollama-prompt')!;
		prompt.value = 'Hello';
		prompt.dispatchEvent(new Event('input'));
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelector('#ollama-model')!.textContent).toContain('llama3.2:latest');
		expect(root.querySelector('.messages')!.textContent).toContain('Hello from Ollama');
		expect(fetchLocalApi).toHaveBeenCalledWith('/api/ollama/chat', {
			model: 'llama3.2:latest', messages: [{ role: 'user', content: 'Hello' }],
		});
	});
});