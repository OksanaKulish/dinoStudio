import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CodeGeneratorComponent } from './code-generator';

describe('CodeGeneratorComponent', () => {
	let fixture: ComponentFixture<CodeGeneratorComponent>;
	let fetchMock: ReturnType<typeof vi.fn>;

	beforeEach(async () => {
		fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.endsWith('/api/health')) {
				if (url.includes(':4301/')) return { ok: true, json: async () => ({ service: 'dino-studio', apiVersion: 4 }) } as Response;
				throw new Error('Connection refused');
			}
			if (url.endsWith('/api/code/generate')) return { ok: true, json: async () => ({ code: 'const answer = 42;', device: 'CPU' }) } as Response;
			throw new Error(`Unexpected request: ${url} ${init?.method ?? ''}`);
		});
		vi.stubGlobal('fetch', fetchMock);
		await TestBed.configureTestingModule({ imports: [CodeGeneratorComponent] }).compileComponents();
		fixture = TestBed.createComponent(CodeGeneratorComponent);
		fixture.detectChanges();
		await fixture.whenStable();
		fixture.detectChanges();
	});

	afterEach(() => vi.unstubAllGlobals());

	it('generates code using the selected Hugging Face model and language', async () => {
		const root: HTMLElement = fixture.nativeElement;
		const prompt = root.querySelector<HTMLTextAreaElement>('#code-request')!;
		prompt.value = 'Create a constant named answer';
		prompt.dispatchEvent(new Event('input'));
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelector('.code-result code')!.textContent).toBe('const answer = 42;');
		expect(root.querySelector('.result-heading')!.textContent).toContain('CPU');
		const request = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/code/generate'));
		expect(request).toBeTruthy();
		expect(JSON.parse(request![1]!.body as string)).toEqual({
			model: 'Qwen/Qwen2.5-Coder-0.5B-Instruct',
			language: 'TypeScript',
			prompt: 'Create a constant named answer',
		});
	});

	it('shows model loading and token generation progress', async () => {
		let finishGeneration!: (response: Response) => void;
		fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			if (url.endsWith('/api/health')) {
				if (url.includes(':4301/')) return { ok: true, json: async () => ({ service: 'dino-studio', apiVersion: 4 }) } as Response;
				throw new Error('Connection refused');
			}
			if (url.endsWith('/api/code/progress')) {
				return { ok: true, json: async () => ({ phase: 'generating', progress: 37 }) } as Response;
			}
			if (url.endsWith('/api/code/generate')) {
				return new Promise(resolve => { finishGeneration = resolve; });
			}
			throw new Error(`Unexpected request: ${url} ${init?.method ?? ''}`);
		});
		const root: HTMLElement = fixture.nativeElement;
		const prompt = root.querySelector<HTMLTextAreaElement>('#code-request')!;
		prompt.value = 'Create a constant named answer';
		prompt.dispatchEvent(new Event('input'));
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		fixture.detectChanges();
		expect(root.querySelector('progress')!.hasAttribute('value')).toBe(false);

		await new Promise(resolve => setTimeout(resolve, 800));
		fixture.detectChanges();
		expect(root.querySelector('progress')!.value).toBe(37);
		expect(root.querySelector('.generation-progress')!.textContent).toContain('37%');

		finishGeneration({ ok: true, json: async () => ({ code: 'const answer = 42;', device: 'CPU' }) } as Response);
		await fixture.whenStable();
		fixture.detectChanges();
		expect(root.querySelector('progress')).toBeNull();
	});
});