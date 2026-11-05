import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GithubProjectComponent } from './github-project';

describe('GithubProjectComponent', () => {
	let fixture: ComponentFixture<GithubProjectComponent>;

	beforeEach(async () => {
		await TestBed.configureTestingModule({ imports: [GithubProjectComponent] }).compileComponents();
		fixture = TestBed.createComponent(GithubProjectComponent);
		fixture.detectChanges();
	});

	afterEach(() => vi.unstubAllGlobals());

	it('loads a public repository tree and previews a selected file', async () => {
		const fetchMock = vi.fn()
			.mockResolvedValueOnce({ ok: true, json: async () => ({ default_branch: 'main' }) })
			.mockResolvedValueOnce({ ok: true, json: async () => ({
				truncated: false,
				tree: [{ path: 'src', type: 'tree' }, { path: 'src/deep', type: 'tree' },
					{ path: 'src/deep/example.ts', type: 'blob', size: 20 }],
			}) })
			.mockResolvedValueOnce({ ok: true, text: async () => 'export const value = 42;' });
		vi.stubGlobal('fetch', fetchMock);

		const root: HTMLElement = fixture.nativeElement;
		const input = root.querySelector<HTMLInputElement>('#github-url')!;
		input.value = 'https://github.com/example/project';
		input.dispatchEvent(new Event('input'));
		root.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelector('.repo-tree')!.textContent).toContain('deep');
		root.querySelector<HTMLButtonElement>('.repo-tree .file')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelector('.file-preview')!.textContent).toContain('export const value = 42;');
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});
});