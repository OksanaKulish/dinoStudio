import { Injectable } from '@angular/core';
import JSZip from 'jszip';

export interface ZippedProject {
	name: string;
	archive: Blob;
	fileCount: number;
	uncompressedBytes: number;
}

interface ZipStats {
	files: number;
	bytes: number;
}

export type ZipProgress = (percent: number) => void;
export interface ScanProgress {
	files: number;
	bytes: number;
	path: string;
	processedFiles?: number;
	totalFiles?: number;
}
export type ScanCallback = (progress: ScanProgress) => void;

interface DirectoryPickerWindow extends Window {
	showDirectoryPicker?: () => Promise<FileSystemDirectoryHandle>;
}

const MAX_FILES = 10_000;
const MAX_UNCOMPRESSED_BYTES = 500_000_000;
const MAX_ARCHIVE_BYTES = 50_000_000;
const SKIP_DIRECTORIES = new Set([
	'.git', '.github', '.vs', '.vscode', '.idea', 'bin', 'obj', 'packages', 'testresults', 'publish',
	'node_modules', 'dist', '.angular', 'coverage', 'out-tsc', '.nx', '.cache', 'migrations', '__pycache__', '.venv', 'venv',
]);

@Injectable({ providedIn: 'root' })
export class FolderZipper {
	async pickDirectory(onProgress?: ZipProgress, onScan?: ScanCallback): Promise<ZippedProject> {
		const pickerWindow = window as DirectoryPickerWindow;
		if (!pickerWindow.showDirectoryPicker) {
			throw new Error('This browser requires the folder input fallback.');
		}
		const handle = await pickerWindow.showDirectoryPicker();
		const zip = new JSZip();
		const stats: ZipStats = { files: 0, bytes: 0 };
		await this.addDirectory(handle, zip, '', stats, onScan);
		onScan?.({ ...stats, path: handle.name });
		return this.finish(handle.name, zip, stats, onProgress);
	}

	async zipFileList(files: FileList, onProgress?: ZipProgress, onScan?: ScanCallback): Promise<ZippedProject> {
		const zip = new JSZip();
		const stats: ZipStats = { files: 0, bytes: 0 };
		const selectedFiles = Array.from(files);
		let projectName = 'project';
		onScan?.({ ...stats, path: 'Selected folder', processedFiles: 0, totalFiles: selectedFiles.length });
		for (const [index, file] of selectedFiles.entries()) {
			const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
			const parts = relative?.replace(/\\/g, '/').split('/').filter(Boolean) ?? [];
			if (parts.length) projectName = parts[0];
			const archivePath = parts.length > 1 ? parts.slice(1).join('/') : file.name;
			if (archivePath && !parts.some(part => SKIP_DIRECTORIES.has(part.toLowerCase()))) {
				this.addFile(zip, archivePath, file, stats);
			}
			const processedFiles = index + 1;
			if (selectedFiles.length <= 25 || processedFiles % 25 === 0) {
				onScan?.({ ...stats, path: parts.join('/') || file.name, processedFiles, totalFiles: selectedFiles.length });
				await new Promise<void>(resolve => setTimeout(resolve, 0));
			}
		}
		onScan?.({ ...stats, path: projectName, processedFiles: selectedFiles.length, totalFiles: selectedFiles.length });
		return this.finish(projectName, zip, stats, onProgress);
	}

	private async addDirectory(
		handle: FileSystemDirectoryHandle,
		zip: JSZip,
		prefix: string,
		stats: ZipStats,
		onScan?: ScanCallback,
	): Promise<void> {
		onScan?.({ ...stats, path: prefix || handle.name });
		// @ts-ignore TypeScript DOM library versions vary in FileSystemDirectoryHandle.values support.
		for await (const entry of handle.values()) {
			if (SKIP_DIRECTORIES.has(entry.name.toLowerCase())) continue;
			const path = prefix ? `${prefix}/${entry.name}` : entry.name;
			if (entry.kind === 'directory') {
				await this.addDirectory(entry as FileSystemDirectoryHandle, zip, path, stats, onScan);
			} else {
				const file = await (entry as FileSystemFileHandle).getFile();
				this.addFile(zip, path, file, stats);
				if (stats.files % 25 === 0) onScan?.({ ...stats, path });
			}
		}
	}

	private addFile(zip: JSZip, path: string, file: File, stats: ZipStats): void {
		stats.files += 1;
		stats.bytes += file.size;
		if (stats.files > MAX_FILES) throw new Error(`Folder contains more than ${MAX_FILES} files.`);
		if (stats.bytes > MAX_UNCOMPRESSED_BYTES) throw new Error('Folder contents exceed the 500 MB limit.');
		zip.file(path, file);
	}

	private async finish(name: string, zip: JSZip, stats: ZipStats, onProgress?: ZipProgress): Promise<ZippedProject> {
		if (!stats.files) throw new Error('The selected folder has no files after ignored directories are skipped.');
		onProgress?.(0);
		const archive = await zip.generateAsync({
			type: 'blob',
			compression: 'DEFLATE',
			compressionOptions: { level: 3 },
		}, metadata => onProgress?.(Math.round(metadata.percent)));
		if (archive.size > MAX_ARCHIVE_BYTES) throw new Error('Compressed project ZIP exceeds the 50 MB limit.');
		return { name, archive, fileCount: stats.files, uncompressedBytes: stats.bytes };
	}
}