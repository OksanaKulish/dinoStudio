import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FolderTreeComponent } from './folder-tree.component';
import { buildTreeFromFiles } from './folder-tree';

describe('FolderTree', () => {
	let component: FolderTreeComponent;
	let fixture: ComponentFixture<FolderTreeComponent>;

	beforeEach(async () => {
		await TestBed.configureTestingModule({
			imports: [FolderTreeComponent],
		}).compileComponents();

		fixture = TestBed.createComponent(FolderTreeComponent);
		component = fixture.componentInstance;
		await fixture.whenStable();
	});

	it('should create', () => {
		expect(component).toBeTruthy();
	});

	it('shows nested uploaded folders and opens their files', () => {
		const file = new File(['hello'], 'notes.txt');
		Object.defineProperty(file, 'webkitRelativePath', { value: 'project/src/deep/notes.txt' });
		const nodes = buildTreeFromFiles([file] as unknown as FileList);
		fixture.componentRef.setInput('nodes', nodes);
		fixture.detectChanges();

		expect(nodes[0].name).toBe('src');
		expect(fixture.nativeElement.textContent).toContain('deep');
		const opened = vi.fn();
		component.fileOpened.subscribe(opened);
		fixture.nativeElement.querySelector('.file').click();
		expect(opened).toHaveBeenCalledWith(nodes[0].children![0].children![0]);
	});
});
