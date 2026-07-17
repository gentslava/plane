/**
 * PP-3: PageFolderStore — MobX store for wiki folder hierarchy.
 * Manages folder CRUD, page-to-folder mapping, and tree state (expanded/collapsed).
 *
 * NOTE: expandedFolders uses observable.ref with immutable replacement.
 * This ensures MobX observer components always re-render on toggle,
 * avoiding issues with ObservableMap tracking in nested observer components.
 */

import { unset } from "lodash-es";
import { makeObservable, observable, runInAction, action, computed, set } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { TPageFolder, TPageFolderCreatePayload, TPageFolderUpdatePayload } from "./iw-page-folder.types";
// service
import { PageFolderService } from "@/services/page/iw-page-folder.service";

type TLoader = "init-loader" | "mutation-loader" | undefined;

const MAX_NESTING_DEPTH = 4;

// localStorage key for expanded state (UI preference — stays in localStorage)
const EXPANDED_STATE_KEY = "iw_page_folders_expanded";

function loadExpandedState(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(EXPANDED_STATE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveExpandedState(state: Record<string, boolean>): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(EXPANDED_STATE_KEY, JSON.stringify(state));
  } catch {
    // silently ignore
  }
}

export interface IPageFolderStore {
  // observables
  loader: TLoader;
  folders: Record<string, TPageFolder>;
  expandedFolders: Record<string, boolean>;
  pageFolderMap: Record<string, string | null>; // pageId => folderId (null = root)
  // computed
  rootFolderIds: string[];
  // helpers
  getFolderById: (folderId: string) => TPageFolder | undefined;
  getChildFolderIds: (parentFolderId: string | null) => string[];
  getPageIdsInFolder: (folderId: string | null) => string[];
  getFolderDepth: (folderId: string) => number;
  getFolderPath: (folderId: string | null) => TPageFolder[];
  getPageFolderId: (pageId: string) => string | null;
  isFolderExpanded: (folderId: string) => boolean;
  // actions
  toggleFolderExpanded: (folderId: string) => void;
  setFolderExpanded: (folderId: string, expanded: boolean) => void;
  fetchFolders: (workspaceSlug: string) => Promise<void>;
  syncPageFolderMap: (pages: Array<{ id?: string | null; folder?: string | null }>) => void;
  createFolder: (workspaceSlug: string, payload: TPageFolderCreatePayload) => Promise<TPageFolder>;
  updateFolder: (workspaceSlug: string, folderId: string, payload: TPageFolderUpdatePayload) => Promise<TPageFolder>;
  removeFolder: (workspaceSlug: string, folderId: string) => Promise<string[]>;
  movePageToFolder: (workspaceSlug: string, pageId: string, folderId: string | null) => Promise<void>;
  removePageFromMap: (pageId: string) => void;
}

export class PageFolderStore implements IPageFolderStore {
  // observables
  loader: TLoader = undefined;
  folders: Record<string, TPageFolder> = {};
  // observable.ref — tracked by reference. Every toggle creates a new object → triggers re-render.
  expandedFolders: Record<string, boolean> = {};
  pageFolderMap: Record<string, string | null> = {};
  // service
  private service: PageFolderService;

  constructor() {
    makeObservable(this, {
      // observables
      loader: observable.ref,
      folders: observable,
      expandedFolders: observable.ref,
      pageFolderMap: observable.ref,
      // computed
      rootFolderIds: computed,
      // actions
      toggleFolderExpanded: action,
      setFolderExpanded: action,
      fetchFolders: action,
      syncPageFolderMap: action,
      createFolder: action,
      updateFolder: action,
      removeFolder: action,
      movePageToFolder: action,
      removePageFromMap: action,
    });
    this.service = new PageFolderService();
    // Load expanded state from localStorage (UI preference only)
    this.expandedFolders = loadExpandedState();
  }

  /**
   * Root-level folder IDs (no parent), sorted alphabetically.
   */
  get rootFolderIds(): string[] {
    return Object.values(this.folders)
      .filter((f) => f.parent_folder === null)
      .sort((a: TPageFolder, b: TPageFolder) => a.name.localeCompare(b.name))
      .map((f: TPageFolder) => f.id);
  }

  /**
   * Get folder by ID.
   */
  getFolderById = computedFn((folderId: string): TPageFolder | undefined => this.folders[folderId]);

  /**
   * Get child folder IDs of a given parent, sorted alphabetically.
   */
  getChildFolderIds = computedFn((parentFolderId: string | null): string[] =>
    Object.values(this.folders)
      .filter((f) => f.parent_folder === parentFolderId)
      .sort((a: TPageFolder, b: TPageFolder) => a.name.localeCompare(b.name))
      .map((f: TPageFolder) => f.id)
  );

  /**
   * Get page IDs inside a specific folder (null = root pages).
   */
  getPageIdsInFolder = (folderId: string | null): string[] => {
    const result: string[] = [];
    for (const [pageId, mappedFolderId] of Object.entries(this.pageFolderMap)) {
      if (mappedFolderId === folderId) {
        result.push(pageId);
      }
    }
    return result;
  };

  /**
   * Compute depth of a folder (1-based: root children are depth 1).
   */
  getFolderDepth = (folderId: string): number => {
    let depth = 0;
    let currentId: string | null = folderId;
    while (currentId) {
      depth++;
      const folder: TPageFolder | undefined = this.folders[currentId];
      currentId = folder?.parent_folder ?? null;
    }
    return depth;
  };

  /**
   * Get the full path of folders from root to the given folder.
   */
  getFolderPath = computedFn((folderId: string | null): TPageFolder[] => {
    const path: TPageFolder[] = [];
    let currentId = folderId;
    while (currentId) {
      const folder = this.folders[currentId];
      if (!folder) break;
      path.unshift(folder);
      currentId = folder.parent_folder;
    }
    return path;
  });

  /**
   * Get the folder ID for a page (null = root).
   */
  getPageFolderId = (pageId: string): string | null => this.pageFolderMap[pageId] ?? null;

  /**
   * Check if a folder is expanded.
   */
  isFolderExpanded = (folderId: string): boolean => !!this.expandedFolders[folderId];

  /**
   * Toggle folder expanded/collapsed state. Replaces the entire object to trigger observable.ref.
   */
  toggleFolderExpanded = (folderId: string): void => {
    const current = !!this.expandedFolders[folderId];
    const next = { ...this.expandedFolders, [folderId]: !current };
    this.expandedFolders = next;
    saveExpandedState(next);
  };

  /**
   * Set folder expanded state explicitly.
   */
  setFolderExpanded = (folderId: string, expanded: boolean): void => {
    const next = { ...this.expandedFolders, [folderId]: expanded };
    this.expandedFolders = next;
    saveExpandedState(next);
  };

  /**
   * Fetch all folders for a workspace from the API.
   */
  fetchFolders = async (workspaceSlug: string): Promise<void> => {
    try {
      const hasData = Object.keys(this.folders).length > 0;
      runInAction(() => {
        this.loader = hasData ? "mutation-loader" : "init-loader";
      });

      const folders = await this.service.fetchAll(workspaceSlug);
      runInAction(() => {
        for (const folder of folders) {
          set(this.folders, folder.id, folder);
        }
        this.loader = undefined;
      });
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
      });
      throw error;
    }
  };

  /**
   * Build pageFolderMap from page objects' `folder` field.
   * Called after pages are loaded from the API.
   */
  syncPageFolderMap = (pages: Array<{ id?: string | null; folder?: string | null }>): void => {
    const next: Record<string, string | null> = {};
    let changed = false;
    for (const page of pages) {
      if (page.id && page.folder) {
        next[page.id] = page.folder;
        if (this.pageFolderMap[page.id] !== page.folder) changed = true;
      }
    }
    // Also check if any existing entries were removed
    if (!changed && Object.keys(this.pageFolderMap).length !== Object.keys(next).length) changed = true;
    // Only replace if something actually changed — prevents infinite render loops
    if (changed) {
      this.pageFolderMap = next;
    }
  };

  /**
   * Create a new folder.
   */
  createFolder = async (workspaceSlug: string, payload: TPageFolderCreatePayload): Promise<TPageFolder> => {
    // Validate nesting depth
    if (payload.parent_folder) {
      const parentDepth = this.getFolderDepth(payload.parent_folder);
      if (parentDepth >= MAX_NESTING_DEPTH) {
        throw new Error(`Cannot nest folders deeper than ${MAX_NESTING_DEPTH} levels.`);
      }
    }

    const folder = await this.service.create(workspaceSlug, payload);
    runInAction(() => {
      set(this.folders, folder.id, folder);
    });
    return folder;
  };

  /**
   * Update a folder (rename, change parent, etc.).
   */
  updateFolder = async (
    workspaceSlug: string,
    folderId: string,
    payload: TPageFolderUpdatePayload
  ): Promise<TPageFolder> => {
    // If moving to a new parent, validate depth
    if (payload.parent_folder !== undefined && payload.parent_folder !== null) {
      const parentDepth = this.getFolderDepth(payload.parent_folder);
      if (parentDepth >= MAX_NESTING_DEPTH) {
        throw new Error(`Cannot nest folders deeper than ${MAX_NESTING_DEPTH} levels.`);
      }
    }

    const folder = await this.service.update(workspaceSlug, folderId, payload);
    runInAction(() => {
      set(this.folders, folderId, folder);
    });
    return folder;
  };

  /**
   * Delete a folder. Backend promotes children to parent and unsets pages.
   */
  removeFolder = async (workspaceSlug: string, folderId: string): Promise<string[]> => {
    const folder = this.folders[folderId];
    if (!folder) return [];

    await this.service.remove(workspaceSlug, folderId);

    const deletedPageIds: string[] = [];

    runInAction(() => {
      // Collect the entire subtree of folder IDs to remove (BFS — mirrors backend)
      const toDelete = new Set<string>();
      const queue = [folderId];
      while (queue.length > 0) {
        const current = queue.shift()!;
        toDelete.add(current);
        for (const f of Object.values(this.folders)) {
          if (f.parent_folder === current) queue.push(f.id);
        }
      }

      // Remove all pages that belonged to any folder in the subtree
      const nextMap = { ...this.pageFolderMap };
      for (const [pageId, mappedFolderId] of Object.entries(nextMap)) {
        if (mappedFolderId && toDelete.has(mappedFolderId)) {
          deletedPageIds.push(pageId);
          delete nextMap[pageId];
        }
      }
      this.pageFolderMap = nextMap;

      // Remove all folders in the subtree
      for (const id of toDelete) unset(this.folders, id);

      // Remove all from expanded state
      const nextExpanded = { ...this.expandedFolders };
      for (const id of toDelete) delete nextExpanded[id];
      this.expandedFolders = nextExpanded;
      saveExpandedState(nextExpanded);
    });

    return deletedPageIds;
  };

  /**
   * Move a page into a folder (or root if folderId is null).
   * Calls the page PATCH endpoint to update the folder field.
   */
  movePageToFolder = async (workspaceSlug: string, pageId: string, folderId: string | null): Promise<void> => {
    await this.service.movePageToFolder(workspaceSlug, pageId, folderId);
    runInAction(() => {
      const next = { ...this.pageFolderMap };
      if (folderId === null) {
        delete next[pageId];
      } else {
        next[pageId] = folderId;
      }
      this.pageFolderMap = next;
    });
  };

  /**
   * Remove a page from the folder mapping (used after page deletion).
   */
  removePageFromMap = (pageId: string): void => {
    const { [pageId]: _, ...rest } = this.pageFolderMap;
    this.pageFolderMap = rest;
  };
}
