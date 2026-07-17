/**
 * PP-3: FolderNode — renders a folder in the wiki sidebar tree.
 * Supports expand/collapse, drag-and-drop (as drop target), context menu, and inline rename.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { runInAction } from "mobx";
import { unset } from "lodash-es";
import { useParams } from "react-router";
import { ChevronRight, Folder, FolderOpen, MoreHorizontal } from "lucide-react";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { AlertModalCore } from "@plane/ui";
import { cn } from "@plane/utils";
// hooks
import { usePageFolders } from "@/hooks/store/use-page-folders";
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { useAppRouter } from "@/hooks/use-app-router";
// local components
import { FolderContextMenu } from "./iw-folder-context-menu";
import { WikiPageNode } from "./iw-page-node";

type Props = {
  folderId: string;
  workspaceSlug: string;
  wikiBasePath: string;
  depth: number;
  onCreatePage: (folderId: string) => void;
  onDeletePage?: (e: React.MouseEvent, pageId: string) => void;
  onDragOver: (e: React.DragEvent, folderId: string) => void;
  onDrop: (e: React.DragEvent, folderId: string) => void;
  dragOverFolderId: string | null;
  currentPageId?: string;
  allPagesList: Array<{ id?: string | null; name?: string; logo_props?: Record<string, unknown> }>;
  autoRename?: boolean;
  onAutoRenameComplete?: () => void;
};

// Store counts from 1 (root=1). MAX_NESTING_DEPTH in store = 4.
// Component counts from 0 (root=0). Component depth 3 = store depth 4 = max allowed.
// Hide "New Sub Folder" at component depth 3 so users can't attempt what the store blocks.
const MAX_DEPTH = 3;

export const FolderNode = observer(function FolderNode(props: Props) {
  const {
    folderId,
    workspaceSlug,
    wikiBasePath,
    depth,
    onCreatePage,
    onDeletePage,
    onDragOver,
    onDrop,
    dragOverFolderId,
    currentPageId,
    allPagesList,
    autoRename = false,
    onAutoRenameComplete,
  } = props;

  const folderStore = usePageFolders();
  const wikiStore = usePageStore(EPageStoreType.WORKSPACE);
  const router = useAppRouter();
  // Get active pageId directly from URL — more reliable than prop chain
  const { pageId: activePageId } = useParams<{ pageId?: string }>();
  const folder = folderStore.folders[folderId];
  // observable.ref — reading the ref directly triggers MobX tracking
  const isExpanded = !!folderStore.expandedFolders[folderId];
  const childFolderIds = folderStore.getChildFolderIds(folderId);
  // Read pageFolderMap ref directly so MobX observer tracks changes
  const pageFolderMap = folderStore.pageFolderMap;
  const pageIdsInFolder: string[] = [];
  for (const [pageId, mappedFolderId] of Object.entries(pageFolderMap)) {
    if (mappedFolderId === folderId) pageIdsInFolder.push(pageId);
  }

  // States
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeletingFolder, setIsDeletingFolder] = useState(false);
  const [newSubFolderId, setNewSubFolderId] = useState<string | null>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Auto-enter rename mode when autoRename flips to true (fires after store update + state update)
  useEffect(() => {
    if (!autoRename) return;
    setRenameValue(folder.name);
    setIsRenaming(true);
    setTimeout(() => {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
      onAutoRenameComplete?.();
    }, 50);
    // folder.name and onAutoRenameComplete intentionally excluded — one-shot on autoRename flip
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRename]);

  // Pages in this folder, sorted alphabetically
  const pagesInFolder = allPagesList
    .filter((p) => p.id && pageIdsInFolder.includes(p.id))
    .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));

  if (!folder) return null;

  const isDragOver = dragOverFolderId === folderId;
  const canCreateSubFolder = depth < MAX_DEPTH;
  const indentPx = depth * 16;

  // Handlers
  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    folderStore.toggleFolderExpanded(folderId);
  };

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const handleMenuClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    // Position menu directly below the button, right-aligned to button's right edge
    setContextMenu({ x: rect.right, y: rect.bottom + 2 });
  };

  const handleStartRename = () => {
    setRenameValue(folder.name);
    setIsRenaming(true);
    setTimeout(() => {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }, 50);
  };

  const handleFinishRename = useCallback(async () => {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== folder.name) {
      try {
        await folderStore.updateFolder(workspaceSlug, folderId, { name: trimmed });
      } catch (error) {
        console.error("Failed to rename folder:", error);
      }
    }
    setIsRenaming(false);
  }, [renameValue, folder.name, folderStore, workspaceSlug, folderId]);

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleFinishRename();
    } else if (e.key === "Escape") {
      setIsRenaming(false);
    }
  };

  const handleDelete = useCallback(() => {
    setIsDeleteModalOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    setIsDeletingFolder(true);
    try {
      // Collect entire subtree of folder IDs BEFORE deletion (BFS)
      const toDelete = new Set<string>();
      const queue = [folderId];
      while (queue.length > 0) {
        const current = queue.shift()!;
        toDelete.add(current);
        for (const f of Object.values(folderStore.folders)) {
          if (f.parent_folder === current) queue.push(f.id);
        }
      }

      // Collect page IDs whose folder is in the subtree — read directly from wikiStore.data
      // so we don't depend on pageFolderMap being in sync
      const pageIdsToDelete = Object.values(wikiStore.data)
        .filter((p) => p && toDelete.has((p as unknown as { folder?: string }).folder ?? ""))
        .map((p) => (p as unknown as { id: string }).id)
        .filter(Boolean);

      await folderStore.removeFolder(workspaceSlug, folderId);

      // Synchronously evict pages from wikiStore so sidebar updates immediately
      runInAction(() => {
        for (const pageId of pageIdsToDelete) {
          unset(wikiStore.data, pageId);
        }
      });

      // If the currently viewed page was inside the deleted subtree, go to wiki home
      if (activePageId && pageIdsToDelete.includes(activePageId)) {
        router.push(wikiBasePath);
      }

      setIsDeleteModalOpen(false);
    } catch (error) {
      console.error("Failed to delete folder:", error);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error!",
        message: "Folder could not be deleted. Please try again.",
      });
    } finally {
      setIsDeletingFolder(false);
    }
  }, [folderStore, wikiStore, router, workspaceSlug, folderId, activePageId, wikiBasePath]);

  const handleNewSubFolder = useCallback(async () => {
    try {
      const newFolder = await folderStore.createFolder(workspaceSlug, {
        name: "New Folder",
        parent_folder: folderId,
      });
      folderStore.setFolderExpanded(folderId, true);
      setNewSubFolderId(newFolder.id);
    } catch (error) {
      console.error("Failed to create sub-folder:", error);
    }
  }, [folderStore, workspaceSlug, folderId]);

  const handleNewPage = () => {
    folderStore.setFolderExpanded(folderId, true);
    onCreatePage(folderId);
  };

  return (
    <div>
      {/* Folder row — treeitem (not button) because it contains nested interactive elements */}
      <div
        role="treeitem"
        aria-expanded={isExpanded}
        tabIndex={0}
        draggable
        style={{ paddingLeft: `${indentPx}px` }}
        className={cn(
          "group flex w-full cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 hover:bg-layer-transparent-hover",
          isDragOver && "ring-primary/30 bg-layer-transparent-hover ring-2"
        )}
        onClick={handleToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleToggle(e as unknown as React.MouseEvent);
        }}
        onContextMenu={handleContextMenu}
        onDragStart={(e) => {
          e.stopPropagation();
          e.dataTransfer.setData("application/x-wiki-folder-id", folderId);
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onDragOver(e, folderId);
        }}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onDrop(e, folderId);
        }}
      >
        {/* Expand/collapse chevron */}
        <ChevronRight
          className={cn(
            "size-4 flex-shrink-0 text-tertiary transition-transform duration-200",
            isExpanded && "rotate-90"
          )}
        />

        {/* Folder icon */}
        {isExpanded ? (
          <FolderOpen className="size-4 flex-shrink-0 text-secondary" />
        ) : (
          <Folder className="size-4 flex-shrink-0 text-secondary" />
        )}

        {/* Folder name or rename input */}
        {isRenaming ? (
          <input
            ref={renameInputRef}
            type="text"
            className="focus:border-primary min-w-0 flex-1 rounded border border-subtle bg-surface-1 px-1.5 py-0.5 text-13 leading-5 font-medium text-primary outline-none"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onBlur={handleFinishRename}
            onKeyDown={handleRenameKeyDown}
          />
        ) : (
          <span className="min-w-0 flex-1 truncate text-13 leading-5 font-medium">{folder.name}</span>
        )}

        {/* Three-dot menu */}
        <button
          type="button"
          className="flex-shrink-0 rounded p-1 text-secondary opacity-0 group-hover:opacity-100 hover:bg-layer-transparent-hover"
          onClick={(e) => {
            e.stopPropagation();
            handleMenuClick(e);
          }}
          title="Folder actions"
        >
          <MoreHorizontal className="size-4" />
        </button>
      </div>

      {/* Context menu */}
      {contextMenu && (
        <FolderContextMenu
          isOpen
          position={contextMenu}
          onClose={() => setContextMenu(null)}
          onRename={handleStartRename}
          onDelete={handleDelete}
          onNewSubFolder={handleNewSubFolder}
          onNewPage={handleNewPage}
          canCreateSubFolder={canCreateSubFolder}
        />
      )}

      {/* Delete confirmation modal */}
      {(() => {
        const childCount = childFolderIds.length + pageIdsInFolder.length;
        const modalContent =
          childCount > 0 ? (
            <>
              <span className="font-medium break-words text-primary">{folder.name}</span> and all its contents (
              {childCount} {childCount === 1 ? "item" : "items"}) will be permanently deleted. This cannot be undone.
            </>
          ) : (
            <>
              <span className="font-medium break-words text-primary">{folder.name}</span> will be permanently deleted.
              This cannot be undone.
            </>
          );
        return (
          <AlertModalCore
            isOpen={isDeleteModalOpen}
            handleClose={() => setIsDeleteModalOpen(false)}
            handleSubmit={handleConfirmDelete}
            isSubmitting={isDeletingFolder}
            title="Delete folder"
            content={modalContent}
            primaryButtonText={{ loading: "Deleting...", default: "Delete" }}
          />
        );
      })()}

      {/* Children (sub-folders + pages) */}
      {isExpanded && (
        <div>
          {/* Sub-folders */}
          {childFolderIds.map((childId) => (
            <FolderNode
              key={childId}
              folderId={childId}
              workspaceSlug={workspaceSlug}
              wikiBasePath={wikiBasePath}
              depth={depth + 1}
              onCreatePage={onCreatePage}
              onDeletePage={onDeletePage}
              onDragOver={onDragOver}
              onDrop={onDrop}
              dragOverFolderId={dragOverFolderId}
              currentPageId={currentPageId}
              allPagesList={allPagesList}
              autoRename={childId === newSubFolderId}
              onAutoRenameComplete={() => setNewSubFolderId(null)}
            />
          ))}

          {/* Pages in this folder */}
          {pagesInFolder.map((page) => {
            const pageId = page.id ?? "";
            return (
              <WikiPageNode
                key={pageId}
                pageId={pageId}
                page={page}
                wikiBasePath={wikiBasePath}
                depth={depth + 1}
                isActive={currentPageId === pageId}
                onDelete={onDeletePage}
              />
            );
          })}

          {/* No "empty folder" message — empty folders just show as collapsed */}
        </div>
      )}
    </div>
  );
});
