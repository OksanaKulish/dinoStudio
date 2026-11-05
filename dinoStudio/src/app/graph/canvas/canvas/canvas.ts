import { ChangeDetectionStrategy, Component, inject, OnDestroy, viewChild } from '@angular/core';
import { FCanvasChangeEvent, FCanvasComponent, FFlowModule, FZoomDirective } from '@foblex/flow';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { PointExtensions } from '@foblex/2d';
import { DOCUMENT } from '@angular/common';

import { signal } from '@angular/core';
import { FolderTreeComponent } from '../../../folder-tree/folder-tree.component';
import { TreeNode, readDirectory, buildTreeFromFiles } from '../../../folder-tree/folder-tree';
import { WslTerminalComponent } from '../wsl-terminal/wsl-terminal';
import { NotebookComponent } from '../notebook/notebook';
import { GithubProjectComponent } from '../github-project/github-project';
import { OllamaComponent } from '../ollama/ollama';
import { TextToImageComponent } from '../text-to-image/text-to-image';
import { CodeGeneratorComponent } from '../code-generator/code-generator';
import { DataTableComponent } from '../data-table/data-table';

interface CanvasToolNode {
	id: string;
	kind: 'terminal' | 'notebook' | 'github' | 'ollama' | 'image' | 'code' | 'data';
	position: { x: number; y: number };
}

interface CanvasConnection {
	id: string;
	source: string;
	target: string;
}

type InitialNode = 'folder' | 'terminal' | 'notebook' | 'card' | 'video';

@Component({
	selector: 'app-canvas',
	standalone: true,
	imports: [
    FFlowModule, FZoomDirective,
    MatCardModule, MatButtonModule, MatSelectModule, MatFormFieldModule,
	FolderTreeComponent, WslTerminalComponent, NotebookComponent, GithubProjectComponent, OllamaComponent, TextToImageComponent, CodeGeneratorComponent, DataTableComponent
],
	changeDetection: ChangeDetectionStrategy.OnPush,
	templateUrl: './canvas.html',
	styleUrl: './canvas.scss',
})
export class CanvasComponent implements OnDestroy {
	private readonly _canvas = viewChild(FCanvasComponent);
	private readonly _document = inject(DOCUMENT);
	protected readonly toolNodes = signal<CanvasToolNode[]>([]);
	protected readonly closedInitialNodes = signal<ReadonlySet<InitialNode>>(new Set());
	protected readonly selectedNode = signal<string | null>(null);
	protected readonly connectingFrom = signal<string | null>(null);
	protected readonly connections = signal<CanvasConnection[]>([
		{ id: 'folder-card', source: 'folder', target: 'card' },
		{ id: 'card-video', source: 'card', target: 'video' },
	]);
	private nextToolId = 0;

	protected removeConnection(id: string): void {
		this.connections.update(connections => connections.filter(connection => connection.id !== id));
	}

	protected selectNode(id: string): void {
		this.selectedNode.set(id);
	}

	protected startConnection(id: string): void {
		this.selectedNode.set(id);
		this.connectingFrom.set(this.connectingFrom() === id ? null : id);
	}

	protected finishConnection(id: string): void {
		const source = this.connectingFrom();
		if (!source || source === id) return;
		this.connections.update(connections => connections.some(connection => connection.source === source && connection.target === id)
			? connections : [...connections, { id: `${source}-${id}`, source, target: id }]);
		this.selectedNode.set(id);
	}

	private removeNodeConnections(id: string): void {
		this.connections.update(connections => connections.filter(connection => connection.source !== id && connection.target !== id));
		if (this.selectedNode() === id) this.selectedNode.set(null);
		if (this.connectingFrom() === id) this.connectingFrom.set(null);
	}

	protected closeInitialNode(node: InitialNode): void {
		this.removeNodeConnections(node);
		this.closedInitialNodes.update(closed => new Set([...closed, node]));
		if (node === 'folder') this.clearFolder();
	}

	protected closeTool(id: string): void {
		this.removeNodeConnections(id);
		this.toolNodes.update(nodes => nodes.filter(node => node.id !== id));
	}

	protected addTool(kind: CanvasToolNode['kind']): void {
		const id = `tool-${++this.nextToolId}`;
		const offset = (this.nextToolId - 1) * 32;
		this.toolNodes.update(nodes => [...nodes, { id, kind, position: { x: 200 + offset, y: -120 + offset } }]);
		this._document.defaultView?.requestAnimationFrame(() => this._canvas()?.centerGroupOrNode(id, true));
	}

	protected readonly options = [
		{ key: 'option1', value: 'Option 1' },
		{ key: 'option2', value: 'Option 2' },
		{ key: 'option3', value: 'Option 3' },
	];

	protected loaded(): void {
		this._canvas()?.fitToScreen(PointExtensions.initialize(100, 100), false);
	}

	protected canvasChanged(event: FCanvasChangeEvent): void {
		this._document.documentElement.style.setProperty('--flow-scale', `${event.scale}`);
	}

	public ngOnDestroy(): void {
		this._document.documentElement.style.removeProperty('--flow-scale');
		this.releasePreview();
	}


	// inside the class:
	protected readonly folderName = signal<string | null>(null);
	protected readonly tree = signal<TreeNode[]>([]);
	protected readonly loading = signal(false);
	protected readonly selectedName = signal<string | null>(null);
	protected readonly selectedText = signal<string | null>(null);
	protected readonly selectedUrl = signal<string | null>(null);
	protected readonly selectedImage = signal(false);
	private _dirHandle?: FileSystemDirectoryHandle; // keep it: you'll need it to write files later

	protected async pickFolder(fallbackInput: HTMLInputElement) {
		if ('showDirectoryPicker' in window) {
			try {
				const handle = await (window as any).showDirectoryPicker({ mode: 'readwrite' });
				this.loading.set(true);
				const nodes = await readDirectory(handle);
				this.clearFolder();
				this._dirHandle = handle;
				this.folderName.set(handle.name);
				this.tree.set(nodes);
			} catch (e) {
				// user cancelled: AbortError, ignore
			} finally {
				this.loading.set(false);
			}
		} else {
			fallbackInput.click();
		}
	}

	protected onFallbackPicked(event: Event) {
		const input = event.target as HTMLInputElement;
		const files = input.files;
		if (!files?.length) return;
		const name = files[0].webkitRelativePath.split('/')[0];
		const nodes = buildTreeFromFiles(files);
		this.clearFolder();
		this.folderName.set(name);
		this.tree.set(nodes);
		input.value = '';
	}

	protected onFilePicked(event: Event): void {
		const input = event.target as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		this.clearFolder();
		void this.openFile({ name: file.name, path: file.name, kind: 'file', file });
		input.value = '';
	}

	protected async openFile(node: TreeNode): Promise<void> {
		if (!node.file) return;
		this.releasePreview();
		this.selectedName.set(node.name);
		this.selectedText.set(null);
		try {
			const file = node.file instanceof File ? node.file : await node.file.getFile();
			if (this.selectedName() !== node.name) return;
			this.selectedImage.set(file.type.startsWith('image/'));
			this.selectedUrl.set(URL.createObjectURL(file));
			if (file.size <= 2_000_000 && (file.type.startsWith('text/') || /\.(json|md|js|ts|css|html|xml|svg|txt|csv)$/i.test(file.name))) {
				this.selectedText.set(await file.text());
			}
		} catch {
			this.selectedText.set('Unable to open this file.');
		}
	}

	private releasePreview(): void {
		const url = this.selectedUrl();
		if (url) URL.revokeObjectURL(url);
		this.selectedUrl.set(null);
	}

	protected clearFolder() {
		this.releasePreview();
		this._dirHandle = undefined;
		this.folderName.set(null);
		this.tree.set([]);
		this.selectedName.set(null);
		this.selectedText.set(null);
		this.selectedImage.set(false);
	}
}