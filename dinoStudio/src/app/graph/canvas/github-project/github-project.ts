import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { FolderTreeComponent } from '../../../folder-tree/folder-tree.component';
import { TreeNode } from '../../../folder-tree/folder-tree';

interface GitHubEntry {
	path: string;
	type: string;
	size?: number;
}

interface Repository {
	owner: string;
	name: string;
	branch: string;
}

@Component({
	selector: 'app-github-project',
	standalone: true,
	imports: [FolderTreeComponent],
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './github-project.html',
	styleUrl: './github-project.scss',
})
export class GithubProjectComponent {
	protected readonly url = signal('');
	protected readonly repository = signal<Repository | null>(null);
	protected readonly nodes = signal<TreeNode[]>([]);
	protected readonly loading = signal(false);
	protected readonly error = signal('');
	protected readonly warning = signal('');
	protected readonly selectedFile = signal('');
	protected readonly fileUrl = signal('');
	protected readonly preview = signal<string | null>(null);
	private readonly sizes = new Map<string, number>();
	private requestId = 0;

	protected async load(): Promise<void> {
		if (this.loading()) return;
		this.error.set('');
		this.warning.set('');
		this.selectedFile.set('');
		this.preview.set(null);
		this.fileUrl.set('');
		const requestId = ++this.requestId;
		try {
			const url = new URL(this.url().includes('://') ? this.url() : `https://github.com/${this.url()}`);
			const parts = url.pathname.split('/').filter(Boolean);
			if (url.protocol !== 'https:' || url.hostname !== 'github.com' || parts.length !== 2 ||
				!/^[-\w]+$/.test(parts[0]) || !/^[-\w.]+$/.test(parts[1])) {
				throw new Error('Enter a public GitHub repository URL or owner/repository.');
			}
			const owner = parts[0];
			const name = parts[1].replace(/\.git$/, '');
			this.loading.set(true);
			const details = await this.getJson<{ default_branch: string }>(`https://api.github.com/repos/${owner}/${name}`);
			const response = await this.getJson<{ tree: GitHubEntry[]; truncated: boolean }>(
				`https://api.github.com/repos/${owner}/${name}/git/trees/${encodeURIComponent(details.default_branch)}?recursive=1`);
			if (requestId !== this.requestId) return;
			this.repository.set({ owner, name, branch: details.default_branch });
			this.sizes.clear();
			for (const entry of response.tree) if (entry.size !== undefined) this.sizes.set(entry.path, entry.size);
			this.nodes.set(this.buildTree(response.tree));
			if (response.truncated) this.warning.set('GitHub truncated this repository tree.');
		} catch (error) {
			this.error.set(error instanceof Error ? error.message : 'Unable to load repository.');
		} finally {
			this.loading.set(false);
		}
	}

	protected async openFile(node: TreeNode): Promise<void> {
		const repository = this.repository();
		if (!repository || node.kind !== 'file') return;
		const requestId = ++this.requestId;
		this.selectedFile.set(node.path);
		this.preview.set(null);
		const path = node.path.split('/').map(encodeURIComponent).join('/');
		this.fileUrl.set(`https://github.com/${repository.owner}/${repository.name}/blob/${encodeURIComponent(repository.branch)}/${path}`);
		if ((this.sizes.get(node.path) ?? Infinity) > 512_000) return;
		try {
			const response = await fetch(`https://raw.githubusercontent.com/${repository.owner}/${repository.name}/${encodeURIComponent(repository.branch)}/${path}`);
			if (!response.ok) throw new Error('File preview unavailable.');
			const text = await response.text();
			if (requestId === this.requestId) this.preview.set(text.slice(0, 512_000));
		} catch {
			if (requestId === this.requestId) this.preview.set('File preview unavailable.');
		}
	}

	private async getJson<T>(url: string): Promise<T> {
		const response = await fetch(url, { headers: { Accept: 'application/vnd.github+json' } });
		if (!response.ok) throw new Error(response.status === 403 ? 'GitHub API rate limit reached. Try again later.' : `GitHub returned ${response.status}.`);
		return response.json() as Promise<T>;
	}

	private buildTree(entries: GitHubEntry[]): TreeNode[] {
		const roots: TreeNode[] = [];
		const paths = new Map<string, TreeNode>();
		for (const entry of entries) {
			if (entry.type !== 'tree' && entry.type !== 'blob') continue;
			const parts = entry.path.split('/');
			for (let index = 0; index < parts.length; index++) {
				const path = parts.slice(0, index + 1).join('/');
				if (paths.has(path)) continue;
				const parent = paths.get(parts.slice(0, index).join('/'));
				const isFile = index === parts.length - 1 && entry.type === 'blob';
				const node: TreeNode = { name: parts[index], path, kind: isFile ? 'file' : 'directory', children: isFile ? undefined : [] };
				(parent?.children ?? roots).push(node);
				paths.set(path, node);
			}
		}
		const sort = (nodes: TreeNode[]): void => {
			nodes.sort((first, second) => first.kind === second.kind
				? first.name.localeCompare(second.name) : first.kind === 'directory' ? -1 : 1);
			for (const node of nodes) if (node.children) sort(node.children);
		};
		sort(roots);
		return roots;
	}
}