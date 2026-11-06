import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CanvasComponent } from './canvas';

describe('CanvasComponent', () => {
	let component: CanvasComponent;
	let fixture: ComponentFixture<CanvasComponent>;

	beforeEach(async () => {
		await TestBed.configureTestingModule({
			imports: [CanvasComponent],
		}).compileComponents();

		fixture = TestBed.createComponent(CanvasComponent);
		component = fixture.componentInstance;
		await fixture.whenStable();
	});

	it('should create', () => {
		expect(component).toBeTruthy();
	});

	it('opens a selected local text file', async () => {
		const file = new File(['hello world'], 'notes.txt', { type: 'text/plain' });
		const input: HTMLInputElement = fixture.nativeElement.querySelector('input:not([webkitdirectory])');
		Object.defineProperty(input, 'files', { configurable: true, value: [file] });
		input.dispatchEvent(new Event('change'));
		await fixture.whenStable();
		fixture.detectChanges();

		expect(fixture.nativeElement.querySelector('.file-preview').textContent).toContain('hello world');
	});

	it('adds independent terminals and notebooks from the palette', async () => {
		const palette: HTMLElement = fixture.nativeElement.querySelector('.component-palette');
		const [addTerminal, addNotebook, addGithub, addOllama, addImage, addCode, addData] = Array.from(palette.querySelectorAll('button'));

		addTerminal.click();
		addTerminal.click();
		addNotebook.click();
		addGithub.click();
		addOllama.click();
		addImage.click();
		addCode.click();
		addData.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(fixture.nativeElement.querySelectorAll('.terminal-node')).toHaveLength(3);
		expect(fixture.nativeElement.querySelectorAll('.notebook-node')).toHaveLength(2);
		expect(fixture.nativeElement.querySelectorAll('.github-node')).toHaveLength(1);
		expect(fixture.nativeElement.querySelectorAll('.ollama-node')).toHaveLength(1);
		expect(fixture.nativeElement.querySelectorAll('.image-node')).toHaveLength(1);
		expect(fixture.nativeElement.querySelectorAll('.code-generator-node')).toHaveLength(1);
		expect(fixture.nativeElement.querySelectorAll('.data-table-node')).toHaveLength(1);
	});

	it('connects one output to multiple inputs without duplicating links', async () => {
		const root: HTMLElement = fixture.nativeElement;
		root.querySelector<HTMLElement>('.folder-node')!.click();
		root.querySelector<HTMLElement>('.notebook-node')!.click();
		fixture.detectChanges();
		expect(root.querySelectorAll('f-connection')).toHaveLength(2);

		root.querySelector<HTMLButtonElement>('.folder-node .connector-panel-out button')!.click();
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('.notebook-node .connector-panel-in button')!.click();
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('.terminal-node .connector-panel-in button')!.click();
		fixture.detectChanges();
		root.querySelector<HTMLButtonElement>('.notebook-node .connector-panel-in button')!.click();
		await fixture.whenStable();
		fixture.detectChanges();

		expect(root.querySelectorAll('f-connection')).toHaveLength(4);
		expect(root.querySelector<HTMLButtonElement>('.terminal-node .connector-panel-in button')!.disabled).toBe(false);
		root.querySelector<HTMLButtonElement>('.folder-node .connector-panel-out button')!.click();
		fixture.detectChanges();
		expect(root.querySelector<HTMLButtonElement>('.notebook-node .connector-panel-in button')!.disabled).toBe(true);
	});
});
