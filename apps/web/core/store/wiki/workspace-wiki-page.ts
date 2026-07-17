/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { computed, makeObservable } from "mobx";
// constants
import { EPageAccess, EUserPermissions } from "@plane/constants";
import type { TPage } from "@plane/types";
// plane web store
import type { RootStore } from "@/store/root.store";
// services
import { WorkspacePageService } from "@/services/page/workspace-page.service";
const workspacePageService = new WorkspacePageService();
// store
import { BasePage } from "../pages/base-page";
import type { TPageInstance } from "../pages/base-page";

export type TWorkspaceWikiPage = TPageInstance;

export class WorkspaceWikiPage extends BasePage implements TWorkspaceWikiPage {
  constructor(store: RootStore, page: TPage) {
    // required fields for API calls
    const { workspaceSlug } = store.router;
    // initialize base instance
    super(store, page, {
      update: async (payload) => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        return await workspacePageService.update(workspaceSlug, page.id, payload);
      },
      updateDescription: async (document) => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        await workspacePageService.updateDescription(workspaceSlug, page.id, document);
      },
      updateAccess: async (payload) => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        await workspacePageService.updateAccess(workspaceSlug, page.id, payload);
      },
      lock: async () => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        await workspacePageService.lock(workspaceSlug, page.id);
      },
      unlock: async () => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        await workspacePageService.unlock(workspaceSlug, page.id);
      },
      archive: async () => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        return await workspacePageService.archive(workspaceSlug, page.id);
      },
      restore: async () => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        await workspacePageService.restore(workspaceSlug, page.id);
      },
      duplicate: async () => {
        if (!workspaceSlug || !page.id) throw new Error("Missing required fields.");
        return await workspacePageService.duplicate(workspaceSlug, page.id);
      },
    });
    makeObservable(this, {
      // computed
      canCurrentUserAccessPage: computed,
      canCurrentUserEditPage: computed,
      canCurrentUserDuplicatePage: computed,
      canCurrentUserLockPage: computed,
      canCurrentUserChangeAccess: computed,
      canCurrentUserArchivePage: computed,
      canCurrentUserDeletePage: computed,
      canCurrentUserFavoritePage: computed,
      canCurrentUserMovePage: computed,
      isContentEditable: computed,
    });
  }

  private getWorkspaceRole(): EUserPermissions | undefined {
    const { workspaceSlug } = this.rootStore.router;
    if (!workspaceSlug) return undefined;
    // getWorkspaceRoleByWorkspaceSlug returns EUserPermissions | EUserWorkspaceRoles — both enums
    // share the same numeric values (ADMIN=20, MEMBER=15, GUEST=5), so casting is safe.
    return this.rootStore.user.permission.getWorkspaceRoleByWorkspaceSlug(workspaceSlug.toString()) as
      | EUserPermissions
      | undefined;
  }

  /**
   * @description returns true if the current logged in user can access the page
   */
  get canCurrentUserAccessPage() {
    const isPagePublic = this.access === EPageAccess.PUBLIC;
    return isPagePublic || this.isCurrentUserOwner;
  }

  /**
   * @description returns true if the current logged in user can edit the page
   */
  get canCurrentUserEditPage() {
    const workspaceRole = this.getWorkspaceRole();
    const isPagePublic = this.access === EPageAccess.PUBLIC;
    return (
      (isPagePublic && !!workspaceRole && workspaceRole >= EUserPermissions.MEMBER) ||
      (!isPagePublic && this.isCurrentUserOwner)
    );
  }

  /**
   * @description returns true if the current logged in user can duplicate the page
   */
  get canCurrentUserDuplicatePage() {
    const workspaceRole = this.getWorkspaceRole();
    return !!workspaceRole && workspaceRole >= EUserPermissions.MEMBER;
  }

  /**
   * @description returns true if the current logged in user can lock the page
   */
  get canCurrentUserLockPage() {
    const workspaceRole = this.getWorkspaceRole();
    return this.isCurrentUserOwner || workspaceRole === EUserPermissions.ADMIN;
  }

  /**
   * @description returns true if the current logged in user can change the access of the page
   */
  get canCurrentUserChangeAccess() {
    const workspaceRole = this.getWorkspaceRole();
    return this.isCurrentUserOwner || workspaceRole === EUserPermissions.ADMIN;
  }

  /**
   * @description returns true if the current logged in user can archive the page
   */
  get canCurrentUserArchivePage() {
    const workspaceRole = this.getWorkspaceRole();
    return this.isCurrentUserOwner || workspaceRole === EUserPermissions.ADMIN;
  }

  /**
   * @description returns true if the current logged in user can delete the page
   */
  get canCurrentUserDeletePage() {
    const workspaceRole = this.getWorkspaceRole();
    return this.isCurrentUserOwner || workspaceRole === EUserPermissions.ADMIN;
  }

  /**
   * @description returns true if the current logged in user can favorite the page
   */
  get canCurrentUserFavoritePage() {
    const workspaceRole = this.getWorkspaceRole();
    return !!workspaceRole && workspaceRole >= EUserPermissions.MEMBER;
  }

  /**
   * @description returns true if the current logged in user can move the page
   */
  get canCurrentUserMovePage() {
    return false; // workspace pages don't support moving between projects
  }

  /**
   * @description returns true if the page can be edited
   */
  get isContentEditable() {
    const workspaceRole = this.getWorkspaceRole();
    const isOwner = this.isCurrentUserOwner;
    const isPublic = this.access === EPageAccess.PUBLIC;
    const isArchived = this.archived_at;
    const isLocked = this.is_locked;

    return (
      !isArchived && !isLocked && (isOwner || (isPublic && !!workspaceRole && workspaceRole >= EUserPermissions.MEMBER))
    );
  }

  getRedirectionLink = () => {
    const { workspaceSlug } = this.rootStore.router;
    return `/${workspaceSlug}/wiki/${this.id}`;
  };
}
