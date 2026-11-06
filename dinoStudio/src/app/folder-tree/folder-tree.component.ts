import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';
import { TreeNode } from './folder-tree';

@Component({
	selector: 'app-folder-tree',
	standalone: true,
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './folder-tree.html',
	styleUrl: './folder-tree.scss',
})
export class FolderTreeComponent {
	nodes = input.required<TreeNode[]>();
	fileOpened = output<TreeNode>();
	protected closed = signal<Set<string>>(new Set());

	protected toggle(path: string) {
		this.closed.update(s => {
			const n = new Set(s);
			n.has(path) ? n.delete(path) : n.add(path);
			return n;
		});
	}
}