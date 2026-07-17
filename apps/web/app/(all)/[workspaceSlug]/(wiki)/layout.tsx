/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useState } from "react";
import { observer } from "mobx-react";
import { Outlet, useParams } from "react-router";
import { Plus, Loader2 } from "lucide-react";
// plane imports
import { WikiIcon } from "@plane/propel/icons";
// components
import { AppSidebarToggleButton } from "@/components/sidebar/sidebar-toggle-button";
import { ProjectsAppPowerKProvider } from "@/components/power-k/projects-app-provider";
// hooks
import { useAppTheme } from "@/hooks/store/use-app-theme";
import { EPageStoreType, usePageStore } from "@/hooks/store";
import { usePageFolders } from "@/hooks/store/use-page-folders";
import { useAppRouter } from "@/hooks/use-app-router";
// local imports
import { WikiAppSidebar } from "./wiki-sidebar";

function WikiLayout() {
  const { workspaceSlug } = useParams();
  const router = useAppRouter();
  const { sidebarCollapsed } = useAppTheme();
  const wikiStore = usePageStore(EPageStoreType.WORKSPACE);
  const folderStore = usePageFolders();
  const { createPage } = wikiStore;

  const slug = workspaceSlug?.toString() ?? "";
  const wikiBasePath = `/${slug}/wiki`;

  // Get current pageId from URL — layout wraps /wiki and /wiki/[pageId]
  const { pageId: currentPageId } = useParams();
  // Get the folder of the currently viewed page (null = root)
  const currentFolderId = currentPageId ? folderStore.getPageFolderId(currentPageId) : null;

  const [isCreating, setIsCreating] = useState(false);

  // Context-aware: create page in the same folder as the currently viewed page
  const handleCreatePage = useCallback(async () => {
    if (!slug || isCreating) return;
    setIsCreating(true);
    try {
      const page = await createPage({ name: "Untitled" });
      if (page?.id) {
        // If the current page is inside a folder, put the new page in that folder too
        if (currentFolderId) {
          await folderStore.movePageToFolder(slug, page.id, currentFolderId);
        }
        router.push(`${wikiBasePath}/${page.id}`);
      }
    } catch (error) {
      console.error("Failed to create wiki page:", error);
    } finally {
      setIsCreating(false);
    }
  }, [slug, isCreating, createPage, router, wikiBasePath, currentFolderId, folderStore]);

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden rounded-lg border border-subtle">
      <ProjectsAppPowerKProvider />
      <div className="relative flex size-full overflow-hidden">
        <WikiAppSidebar />
        <main className="relative flex h-full w-full flex-col overflow-hidden bg-surface-1">
          {/* Top header bar */}
          <div className="flex items-center justify-between gap-2 border-b border-subtle px-4 py-2">
            <div className="flex items-center gap-2">
              {sidebarCollapsed && <AppSidebarToggleButton />}
              {/* Permanent Wiki section heading */}
              <div className="text-sm flex items-center gap-1.5 font-medium text-primary">
                <WikiIcon className="size-4 flex-shrink-0" />
                <span>Wiki</span>
              </div>
            </div>
            <button
              type="button"
              className="flex flex-shrink-0 items-center gap-1.5 rounded-md border border-subtle px-3 py-1.5 text-13 font-medium text-secondary hover:bg-layer-transparent-hover disabled:cursor-not-allowed disabled:opacity-50"
              onClick={handleCreatePage}
              disabled={isCreating}
            >
              {isCreating ? (
                <Loader2 className="size-3.5 flex-shrink-0 animate-spin" />
              ) : (
                <Plus className="size-3.5 flex-shrink-0" />
              )}
              <span>{isCreating ? "Creating..." : "New page"}</span>
            </button>
          </div>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default observer(WikiLayout);
