import { ComponentFixture, TestBed } from '@angular/core/testing';
import { fetchLocalApi } from '../../../local-api';
import { TextToImageComponent } from './text-to-image';

vi.mock('../../../local-api', () => ({ fetchLocalApi: vi.fn() }));

describe('TextToImageComponent', () => {
	let fixture: ComponentFixture<TextToImageComponent>;

	beforeEach(async () => {
		await TestBed.configureTestingModule({ imports: [TextToImageComponent] }).compileComponents();
		fixture = TestBed.createComponent(TextToImageComponent);
		fixture.detectChanges();
	});

	afterEach(() => vi.resetAllMocks());

	it('generates and exposes a downloadable image for the prompt', async () => {
		vi.mocked(fetchLocalApi).mockResolvedValueOnce({
			ok: true, json: async () => ({ image: 'AA==', device: 'CPU' }),
		} as Response);
		const root: HTMLElement = fixture.nativeElement;
		const prompt = root.querySelector<HTMLTextAreaElement>('#image-prompt')!;
		prompt.value = 'A green dinosaur in a library';
		prompt.dispatchEvent(new Event('input'));
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelector<HTMLImageElement>('.image-preview img')!.src).toContain('data:image/png;base64,AA==');
		expect(root.querySelector('footer')!.textContent).toContain('CPU');
		expect(fetchLocalApi).toHaveBeenCalledWith('/api/image/generate', { prompt: prompt.value });
	});

	it('shows model loading and then the reported inference percentage', async () => {
		let finishGeneration!: (response: Response) => void;
		vi.mocked(fetchLocalApi)
			.mockImplementationOnce(() => new Promise(resolve => { finishGeneration = resolve; }))
			.mockResolvedValueOnce({ ok: true, json: async () => ({ phase: 'generating', progress: 25 }) } as Response);
		const root: HTMLElement = fixture.nativeElement;
		const prompt = root.querySelector<HTMLTextAreaElement>('#image-prompt')!;
		prompt.value = 'A mountain lake';
		prompt.dispatchEvent(new Event('input'));
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		fixture.detectChanges();

		expect(root.querySelector('progress')!.hasAttribute('value')).toBe(false);
		await new Promise(resolve => setTimeout(resolve, 800));
		fixture.detectChanges();
		expect(root.querySelector('progress')!.value).toBe(25);
		expect(root.querySelector('.generation-progress')!.textContent).toContain('25%');

		finishGeneration({ ok: true, json: async () => ({ image: 'AA==', device: 'CPU' }) } as Response);
		await fixture.whenStable();
		fixture.detectChanges();
		expect(root.querySelector('progress')).toBeNull();
	});
});