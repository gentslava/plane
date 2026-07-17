/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 *
 * PP-3: Updated to support folder hierarchy in the wiki sidebar.
 */

import { useCallback, useEffect, useState } from "react";
import { observer } from "mobx-react";
import { runInAction } from "mobx";
import { unset } from "lodash-es";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { Home, Loader2, Plus, FolderPlus, FileText } from "lucide-react";
import { CustomMenu } from "@plane/ui";
import { WikiIcon } from "@plane/propel/icons";
import { ScrollArea } from "@plane/propel/scrollarea";
import { AlertModalCore } from "@plane/ui";
// components
import { SidebarNavItem } from "@/components/sidebar/sidebar-navigation";
import { AppSidebarToggleButton } from "@/components/sidebar/sidebar-toggle-button";
// store hooks
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { usePageFolders } from "@/hooks/store/use-page-folders";
import { useAppRouter } from "@/hooks/use-app-router";
// local components
import { FolderNode, WikiPageNode } from "./components";

export const WikiSidebarContent = observer(function WikiSidebarContent() {
  const { workspaceSlug } = useParams();
  const pathname = usePathname();
  const router = useAppRouter();
  const wikiStore = usePageStore(EPageStoreType.WORKSPACE);
  const folderStore = usePageFolders();
  const { loader, data, fetchPagesList, createPage, removePage } = wikiStore;

  // All pages list (for passing to folder tree), excluding archived pages
  const pagesList = Object.values(data).filter((p) => !p.archived_at);

  // Read pageFolderMap ref directly so MobX observer tracks it
  const pageFolderMap = folderStore.pageFolderMap;

  // Root-level pages (not in any folder), sorted alphabetically
  const rootPageIds = new Set(
    pagesList
      .filter((p) => {
        const pageId = p.id ?? "";
        return pageId && !pageFolderMap[pageId];
      })
      .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""))
      .map((p) => p.id ?? "")
  );

  const rootPages = pagesList
    .filter((p) => p.id && rootPageIds.has(p.id))
    .sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));

  // Root folder IDs
  const rootFolderIds = folderStore.rootFolderIds;

  // States
  const [isCreating, setIsCreating] = useState(false);
  const [isCreatingFolder, setIsCreatingFolder] = useState(false);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);
  const [newFolderId, setNewFolderId] = useState<string | null>(null);
  const [deletePageId, setDeletePageId] = useState<string | null>(null);
  const [isDeletingPage, setIsDeletingPage] = useState(false);

  const slug = workspaceSlug?.toString() ?? "";
  const wikiBasePath = `/${slug}/wiki`;
  const isHomePath = pathname === `/${slug}/wiki` || pathname === `/${slug}/wiki/`;

  // Extract current pageId from pathname
  const pathParts = pathname.split("/");
  const currentPageId = pathParts.length > 3 ? pathParts[pathParts.length - 1] : undefined;

  // Fetch pages and folders on mount, then sync page-folder map once
  useEffect(() => {
    if (slug) {
      void fetchPagesList(slug).then(() => {
        // Sync page→folder mapping from page objects' `folder` field on initial load only.
        // After this, movePageToFolder maintains the map — don't re-sync or it overwrites local changes.
        const pages = Object.values(wikiStore.data);
        return folderStore.syncPageFolderMap(pages as Array<{ id?: string | null; folder?: string | null }>);
      });
      folderStore.fetchFolders(slug);
    }
  }, [slug, fetchPagesList, folderStore, wikiStore]);

  // Handle new page creation (optionally into a folder)
  const handleCreatePage = useCallback(
    async (folderId?: string) => {
      if (!slug || isCreating) return;
      setIsCreating(true);
      try {
        const page = await createPage({
          name: "Untitled",
        });
        if (page?.id) {
          // If creating in a folder, map the page to the folder
          if (folderId) {
            await folderStore.movePageToFolder(slug, page.id, folderId);
          }
          router.push(`${wikiBasePath}/${page.id}`);
        }
      } catch (error) {
        console.error("Failed to create wiki page:", error);
      } finally {
        setIsCreating(false);
      }
    },
    [slug, isCreating, createPage, router, wikiBasePath, folderStore]
  );

  // Handle new folder creation
  const handleCreateFolder = useCallback(async () => {
    if (!slug || isCreatingFolder) return;
    setIsCreatingFolder(true);
    try {
      const newFolder = await folderStore.createFolder(slug, {
        name: "New Folder",
        parent_folder: null,
      });
      setNewFolderId(newFolder.id);
    } catch (error) {
      console.error("Failed to create folder:", error);
    } finally {
      setIsCreatingFolder(false);
    }
  }, [slug, isCreatingFolder, folderStore]);

  // Handle page deletion
  // Opens the delete confirmation modal — actual deletion happens in handleConfirmDeletePage
  const handleDeletePage = useCallback((e: React.MouseEvent, pageId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDeletePageId(pageId);
  }, []);

  const handleConfirmDeletePage = useCallback(async () => {
    if (!deletePageId || !slug) return;
    setIsDeletingPage(true);
    try {
      await removePage({ pageId: deletePageId });
    } catch {
      // API returns 403 but actually deletes — force remove from store
      runInAction(() => {
        unset(wikiStore.data, [deletePageId]);
      });
    }
    folderStore.removePageFromMap(deletePageId);
    router.push(wikiBasePath);
    setDeletePageId(null);
    setIsDeletingPage(false);
  }, [deletePageId, slug, removePage, wikiStore, wikiBasePath, router, folderStore]);

  // Drag and drop handlers
  const handleDragOver = useCallback((_e: React.DragEvent, folderId: string) => {
    setDragOverFolderId(folderId);
  }, []);

  // Max nesting levels (matches store's MAX_NESTING_DEPTH; store counts from 1)
  const MAX_NESTING_DEPTH = 4;

  // Returns the max depth of a folder's subtree (0 = leaf, 1 = has children, etc.)
  const getSubtreeHeight = useCallback(
    (folderId: string): number => {
      const children = Object.values(folderStore.folders).filter((f) => f.parent_folder === folderId);
      if (children.length === 0) return 0;
      return 1 + Math.max(...children.map((c) => getSubtreeHeight(c.id)));
    },
    [folderStore.folders]
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent, targetFolderId: string) => {
      e.preventDefault();
      setDragOverFolderId(null);
      if (!slug) return;

      const pageId = e.dataTransfer.getData("application/x-wiki-page-id");
      const draggedFolderId = e.dataTransfer.getData("application/x-wiki-folder-id");

      if (pageId) {
        try {
          await folderStore.movePageToFolder(slug, pageId, targetFolderId);
        } catch (error) {
          console.error("Failed to move page to folder:", error);
        }
      } else if (draggedFolderId && draggedFolderId !== targetFolderId) {
        // Guard: prevent dropping a folder into its own descendant (circular)
        const descendants = new Set<string>();
        const queue = [draggedFolderId];
        while (queue.length > 0) {
          const cur = queue.shift()!;
          descendants.add(cur);
          Object.values(folderStore.folders).forEach((f) => {
            if (f.parent_folder === cur) queue.push(f.id);
          });
        }
        if (descendants.has(targetFolderId)) return;

        // Guard: subtree depth check — target depth + 1 (dragged) + subtree height must not exceed limit
        const targetDepth = folderStore.getFolderDepth(targetFolderId); // store counts from 1
        const subtreeHeight = getSubtreeHeight(draggedFolderId);
        if (targetDepth + 1 + subtreeHeight > MAX_NESTING_DEPTH) return;

        try {
          await folderStore.updateFolder(slug, draggedFolderId, { parent_folder: targetFolderId });
        } catch (error) {
          console.error("Failed to move folder:", error);
        }
      }
    },
    [slug, folderStore, getSubtreeHeight]
  );

  // Handle drop on root area (move page or folder out of any folder → root)
  const handleRootDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOverFolderId("__root__");
  }, []);

  const handleRootDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setDragOverFolderId(null);
      if (!slug) return;

      const pageId = e.dataTransfer.getData("application/x-wiki-page-id");
      const draggedFolderId = e.dataTransfer.getData("application/x-wiki-folder-id");

      if (pageId) {
        try {
          await folderStore.movePageToFolder(slug, pageId, null);
        } catch (error) {
          console.error("Failed to move page to root:", error);
        }
      } else if (draggedFolderId) {
        // Depth check: at root (store depth 0), subtree height must not exceed limit
        const subtreeHeight = getSubtreeHeight(draggedFolderId);
        if (1 + subtreeHeight > MAX_NESTING_DEPTH) return;
        try {
          await folderStore.updateFolder(slug, draggedFolderId, { parent_folder: null });
        } catch (error) {
          console.error("Failed to move folder to root:", error);
        }
      }
    },
    [slug, folderStore, getSubtreeHeight]
  );

  const isInitLoading = loader === "init-loader";

  const deletePageName = deletePageId ? pagesList.find((p) => p.id === deletePageId)?.name || "Untitled" : "";

  return (
    <>
      <AlertModalCore
        handleClose={() => setDeletePageId(null)}
        handleSubmit={handleConfirmDeletePage}
        isSubmitting={isDeletingPage}
        isOpen={!!deletePageId}
        title="Delete page"
        content={
          <p className="text-sm text-secondary">
            <span className="font-medium text-primary">&quot;{deletePageName}&quot;</span> will be permanently deleted.
            This cannot be undone.
          </p>
        }
      />
      <div className="flex h-full w-full animate-fade-in flex-col">
        {/* Header */}
        <div className="flex flex-col gap-2 px-3">
          <div className="flex items-center justify-between gap-2 px-2">
            <div className="flex items-center gap-1.5 pt-1">
              <WikiIcon className="size-4 flex-shrink-0 text-primary" />
              <span className="text-16 font-medium text-primary">Wiki</span>
            </div>
            <div className="flex items-center gap-1">
              <CustomMenu
                customButton={
                  <button
                    type="button"
                    className="flex items-center rounded-md p-1 text-secondary hover:bg-layer-transparent-hover disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={isCreatingFolder || isCreating}
                    title="New"
                  >
                    {isCreatingFolder || isCreating ? (
                      <Loader2 className="size-4 flex-shrink-0 animate-spin" />
                    ) : (
                      <Plus className="size-4 flex-shrink-0" />
                    )}
                  </button>
                }
                placement="bottom-start"
                closeOnSelect
              >
                <CustomMenu.MenuItem onClick={handleCreateFolder}>
                  <span className="flex items-center gap-2">
                    <FolderPlus className="size-3.5 text-secondary" />
                    <span>New folder</span>
                  </span>
                </CustomMenu.MenuItem>
                <CustomMenu.MenuItem onClick={() => handleCreatePage(undefined)}>
                  <span className="flex items-center gap-2">
                    <FileText className="size-3.5 text-secondary" />
                    <span>New page</span>
                  </span>
                </CustomMenu.MenuItem>
              </CustomMenu>
              <AppSidebarToggleButton />
            </div>
          </div>
        </div>

        {/* Page/folder tree */}
        <ScrollArea
          orientation="vertical"
          scrollType="hover"
          size="sm"
          rootClassName="size-full overflow-x-hidden overflow-y-auto"
          viewportClassName="flex flex-col gap-0.5 overflow-x-hidden h-full w-full overflow-y-auto px-3 pt-3 pb-0.5"
        >
          {/* Home page link */}
          <Link href={wikiBasePath}>
            <SidebarNavItem isActive={isHomePath}>
              <div className="flex items-center gap-1.5 py-[1px]">
                <Home className="size-4 flex-shrink-0" />
                <p className="text-13 leading-5 font-medium">Home</p>
              </div>
            </SidebarNavItem>
          </Link>

          {/* Workspace section */}
          <div className="mt-3">
            <div className="px-2 py-1.5">
              <span className="text-13 font-semibold text-placeholder">Workspace</span>
            </div>

            {isInitLoading ? (
              <div className="flex items-center justify-center py-4">
                <Loader2 className="size-4 animate-spin text-placeholder" />
              </div>
            ) : (
              <div className="flex flex-col gap-0.5" onDragOver={handleRootDragOver} onDrop={handleRootDrop}>
                {/* Folders first (alphabetical) */}
                {rootFolderIds.map((folderId) => (
                  <FolderNode
                    key={folderId}
                    folderId={folderId}
                    workspaceSlug={slug}
                    wikiBasePath={wikiBasePath}
                    depth={0}
                    onCreatePage={(fId) => handleCreatePage(fId)}
                    onDeletePage={handleDeletePage}
                    onDragOver={handleDragOver}
                    onDrop={handleDrop}
                    dragOverFolderId={dragOverFolderId}
                    currentPageId={currentPageId}
                    allPagesList={pagesList}
                    autoRename={folderId === newFolderId}
                    onAutoRenameComplete={() => setNewFolderId(null)}
                  />
                ))}

                {/* Root pages (not in any folder, alphabetical) */}
                {rootPages.map((page) => {
                  const pageId = page.id ?? "";
                  return (
                    <WikiPageNode
                      key={pageId}
                      pageId={pageId}
                      page={page}
                      wikiBasePath={wikiBasePath}
                      depth={0}
                      isActive={currentPageId === pageId}
                      onDelete={handleDeletePage}
                    />
                  );
                })}

                {/* Empty state */}
                {rootFolderIds.length === 0 && rootPages.length === 0 && (
                  <div className="px-2 py-4 text-center text-13 text-placeholder">
                    No pages yet. Create your first wiki page.
                  </div>
                )}
              </div>
            )}
          </div>
        </ScrollArea>
      </div>
    </>
  );
});
