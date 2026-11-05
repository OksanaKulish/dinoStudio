import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NotebookComponent } from './notebook';

describe('NotebookComponent', () => {
	let fixture: ComponentFixture<NotebookComponent>;

	beforeEach(async () => {
		await TestBed.configureTestingModule({ imports: [NotebookComponent] }).compileComponents();
		fixture = TestBed.createComponent(NotebookComponent);
		fixture.detectChanges();
	});

	afterEach(() => vi.unstubAllGlobals());

	it('runs the pandas example and renders a three-column five-row table', async () => {
		const rows = Array.from({ length: 5 }, (_, index) =>
			`<tr><td>${index}</td><td>${index + 1}</td><td>${index + 2}</td></tr>`).join('');
		const html = `<table><thead><tr><th>Alpha</th><th>Beta</th><th>Gamma</th></tr></thead><tbody>${rows}</tbody></table>`;
		const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ results: [[{ text: '', html }]] }) });
		vi.stubGlobal('fetch', fetchMock);

		(fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('button[title="Run notebook"]')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		const table = (fixture.nativeElement as HTMLElement).querySelector('table')!;
		expect(table.querySelectorAll('thead th')).toHaveLength(3);
		expect(table.querySelectorAll('tbody tr')).toHaveLength(5);
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});