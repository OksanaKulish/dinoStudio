export interface TreeNode {
    name: string;
    path: string;
    kind: 'file' | 'directory';
    children?: TreeNode[];
    file?: File | FileSystemFileHandle;
}

// Chrome/Edge: real directory handle
export async function readDirectory(
    handle: FileSystemDirectoryHandle, path = ''
): Promise<TreeNode[]> {
    const nodes: TreeNode[] = [];
    // @ts-ignore: values() is missing from some TS DOM lib versions
    for await (const entry of handle.values()) {
        const p = path ? `${path}/${entry.name}` : entry.name;
        if (entry.kind === 'directory') {
            nodes.push({
                name: entry.name, path: p, kind: 'directory',
                children: await readDirectory(entry as FileSystemDirectoryHandle, p),
            });
        } else {
            nodes.push({ name: entry.name, path: p, kind: 'file', file: entry as FileSystemFileHandle });
        }
    }
    return sortNodes(nodes);
}

// Fallback: FileList from <input webkitdirectory>
export function buildTreeFromFiles(files: FileList): TreeNode[] {
    const root: TreeNode[] = [];
    for (const file of Array.from(files)) {
        const parts = file.webkitRelativePath.split('/').slice(1);
        let level = root;
        parts.forEach((part: string, i: number) => {
            const isFile = i === parts.length - 1;
            let node = level.find(n => n.name === part);
            if (!node) {
                node = {
                    name: part, path: parts.slice(0, i + 1).join('/'),
                    kind: isFile ? 'file' : 'directory',
                    children: isFile ? undefined : [],
                    file: isFile ? file : undefined,
                };
                level.push(node);
            }
            level = node.children ?? level;
        });
    }
    return sortNodes(root);
}

function sortNodes(nodes: TreeNode[]): TreeNode[] {
    nodes.sort((a, b) =>
        a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'directory' ? -1 : 1);
    nodes.forEach(n => n.children && sortNodes(n.children));
    return nodes;
}