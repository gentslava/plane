/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { unset, set } from "lodash-es";
import { makeObservable, observable, runInAction, action, computed } from "mobx";
import { computedFn } from "mobx-utils";
// types
import type { TPage, TPageFilters, TPageNavigationTabs } from "@plane/types";
// helpers
import { filterPagesByPageType, getPageName, orderPages, shouldFilterPage } from "@plane/utils";
// plane web store
import type { RootStore } from "@/store/root.store";
// services
import { WorkspacePageService } from "@/services/page/workspace-page.service";
// store
import type { CoreRootStore } from "../root.store";
import type { TWorkspaceWikiPage } from "./workspace-wiki-page";
import { WorkspaceWikiPage } from "./workspace-wiki-page";

type TLoader = "init-loader" | "mutation-loader" | undefined;

type TError = { title: string; description: string };

export interface IWorkspaceWikiPageStore {
  // observables
  loader: TLoader;
  data: Record<string, TWorkspaceWikiPage>; // pageId => Page
  error: TError | undefined;
  filters: TPageFilters;
  // computed
  isAnyPageAvailable: boolean;
  // helper actions
  getPageIds: () => string[];
  getFilteredPageIdsByTab: (pageType: TPageNavigationTabs) => string[] | undefined;
  getPageById: (pageId: string) => TWorkspaceWikiPage | undefined;
  updateFilters: <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => void;
  clearAllFilters: () => void;
  // actions
  fetchPagesList: (workspaceSlug: string) => Promise<TPage[] | undefined>;
  fetchPageDetails: (
    workspaceSlug: string,
    pageId: string,
    options?: { trackVisit?: boolean }
  ) => Promise<TPage | undefined>;
  createPage: (pageData: Partial<TPage>) => Promise<TPage | undefined>;
  removePage: (params: { pageId: string; shouldSync?: boolean }) => Promise<void>;
}

export class WorkspaceWikiPageStore implements IWorkspaceWikiPageStore {
  // observables
  loader: TLoader = "init-loader";
  data: Record<string, TWorkspaceWikiPage> = {}; // pageId => Page
  error: TError | undefined = undefined;
  filters: TPageFilters = {
    searchQuery: "",
    sortKey: "updated_at",
    sortBy: "desc",
  };
  // service
  service: WorkspacePageService;
  rootStore: CoreRootStore;

  constructor(private store: RootStore) {
    makeObservable(this, {
      // observables
      loader: observable.ref,
      data: observable,
      error: observable,
      filters: observable,
      // computed
      isAnyPageAvailable: computed,
      // helper actions
      updateFilters: action,
      clearAllFilters: action,
      // actions
      fetchPagesList: action,
      fetchPageDetails: action,
      createPage: action,
      removePage: action,
    });
    this.rootStore = store;
    // service
    this.service = new WorkspacePageService();
  }

  /**
   * @description check if any page is available
   */
  get isAnyPageAvailable() {
    if (this.loader) return true;
    return Object.keys(this.data).length > 0;
  }

  /**
   * @description get all page ids sorted
   */
  getPageIds = computedFn(() => {
    const pages = Object.values(this?.data || {});
    const sorted = orderPages(pages, this.filters.sortKey, this.filters.sortBy);
    return (sorted.map((page) => page.id) as string[]) || [];
  });

  /**
   * @description get filtered page ids based on the pageType
   * @param {TPageNavigationTabs} pageType
   */
  getFilteredPageIdsByTab = computedFn((pageType: TPageNavigationTabs) => {
    const pagesByType = filterPagesByPageType(pageType, Object.values(this?.data || {}));
    let filteredPages = pagesByType.filter(
      (p) =>
        getPageName(p.name).toLowerCase().includes(this.filters.searchQuery.toLowerCase()) &&
        shouldFilterPage(p, this.filters.filters)
    );
    filteredPages = orderPages(filteredPages, this.filters.sortKey, this.filters.sortBy);

    const pages = (filteredPages.map((page) => page.id) as string[]) || undefined;

    return pages ?? undefined;
  });

  /**
   * @description get the page store by id
   * @param {string} pageId
   */
  getPageById = computedFn((pageId: string) => this.data?.[pageId] || undefined);

  updateFilters = <T extends keyof TPageFilters>(filterKey: T, filterValue: TPageFilters[T]) => {
    runInAction(() => {
      set(this.filters, [filterKey], filterValue);
    });
  };

  /**
   * @description clear all the filters
   */
  clearAllFilters = () =>
    runInAction(() => {
      set(this.filters, ["filters"], {});
    });

  /**
   * @description fetch all workspace wiki pages
   */
  fetchPagesList = async (workspaceSlug: string) => {
    try {
      if (!workspaceSlug) return undefined;

      const hasExistingData = Object.keys(this.data).length > 0;
      runInAction(() => {
        this.loader = hasExistingData ? `mutation-loader` : `init-loader`;
        this.error = undefined;
      });

      const pages = await this.service.fetchAll(workspaceSlug);
      runInAction(() => {
        for (const page of pages) {
          if (page?.id) {
            const existingPage = this.getPageById(page.id);
            if (existingPage) {
              const { name, ...otherFields } = page;
              existingPage.mutateProperties(otherFields, false);
            } else {
              set(this.data, [page.id], new WorkspaceWikiPage(this.store, page));
            }
          }
        }
        this.loader = undefined;
      });

      return pages;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to fetch the wiki pages, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * @description fetch the details of a wiki page
   * @param {string} pageId
   */
  fetchPageDetails = async (workspaceSlug: string, pageId: string, options?: { trackVisit?: boolean }) => {
    const { trackVisit } = options || {};
    try {
      if (!workspaceSlug || !pageId) return undefined;

      const currentPageId = this.getPageById(pageId);
      runInAction(() => {
        this.loader = currentPageId ? `mutation-loader` : `init-loader`;
        this.error = undefined;
      });

      const page = await this.service.fetchById(workspaceSlug, pageId, trackVisit ?? true);

      runInAction(() => {
        if (page?.id) {
          const pageInstance = this.getPageById(page.id);
          if (pageInstance) {
            pageInstance.mutateProperties(page, false);
          } else {
            set(this.data, [page.id], new WorkspaceWikiPage(this.store, page));
          }
        }
        this.loader = undefined;
      });

      return page;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to fetch the wiki page, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * @description create a wiki page
   * @param {Partial<TPage>} pageData
   */
  createPage = async (pageData: Partial<TPage>) => {
    try {
      const { workspaceSlug } = this.store.router;
      if (!workspaceSlug) return undefined;

      runInAction(() => {
        this.loader = "mutation-loader";
        this.error = undefined;
      });

      const page = await this.service.create(workspaceSlug, pageData);
      runInAction(() => {
        if (page?.id) set(this.data, [page.id], new WorkspaceWikiPage(this.store, page));
        this.loader = undefined;
      });

      return page;
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to create a wiki page, Please try again later.",
        };
      });
      throw error;
    }
  };

  /**
   * @description delete a wiki page
   * @param {string} pageId
   */
  removePage = async ({ pageId, shouldSync: _shouldSync = true }: { pageId: string; shouldSync?: boolean }) => {
    try {
      const { workspaceSlug } = this.store.router;
      if (!workspaceSlug || !pageId) return undefined;

      await this.service.remove(workspaceSlug, pageId);
      runInAction(() => {
        unset(this.data, [pageId]);
        if (this.rootStore.favorite.entityMap[pageId]) this.rootStore.favorite.removeFavoriteFromStore(pageId);
      });
    } catch (error) {
      runInAction(() => {
        this.loader = undefined;
        this.error = {
          title: "Failed",
          description: "Failed to delete a wiki page, Please try again later.",
        };
      });
      throw error;
    }
  };
}
