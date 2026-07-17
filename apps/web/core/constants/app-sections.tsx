/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * IW: app-section registry — single source of truth for top-level app sections
 * consumed by the app switcher and the Power-K command palette.
 */

import type { ComponentType, ReactNode } from "react";
import { Settings as SettingsIcon, Sparkles } from "lucide-react";
import { WikiIcon } from "@plane/propel/icons";
import type { useWorkspacePaths } from "@/hooks/use-workspace-paths";

export type TAppSectionId = "projects" | "wiki" | "ai" | "settings";

type TWorkspacePaths = ReturnType<typeof useWorkspacePaths>;

export type TAppSectionDefinition = {
  id: TAppSectionId;
  label: string;
  i18nKey: string;
  iconNode: ReactNode;
  icon: ComponentType<{ className?: string }>;
  hrefBuilder: (workspaceSlug: string) => string;
  isActiveSelector: (paths: TWorkspacePaths) => boolean;
};

const IWLogoIcon: ComponentType<{ className?: string }> = ({ className }) => (
  <img src="/favicon/iw-icon-32.png" alt="IW" className={className} />
);

export const APP_SECTIONS: TAppSectionDefinition[] = [
  {
    id: "projects",
    label: "Projects",
    i18nKey: "iw.app_switcher.projects",
    iconNode: <img src="/favicon/iw-icon-32.png" alt="IW" className="size-4" />,
    icon: IWLogoIcon,
    hrefBuilder: (slug) => `/${slug}/`,
    isActiveSelector: (paths) => paths.isProjectsPath && !paths.isNotificationsPath,
  },
  {
    id: "wiki",
    label: "Wiki",
    i18nKey: "iw.app_switcher.wiki",
    iconNode: <WikiIcon className="size-4" />,
    icon: WikiIcon,
    hrefBuilder: (slug) => `/${slug}/wiki/`,
    isActiveSelector: (paths) => paths.isWikiPath,
  },
  {
    id: "ai",
    label: "AI",
    i18nKey: "iw.app_switcher.ai",
    iconNode: <Sparkles className="size-4" />,
    icon: Sparkles,
    hrefBuilder: (slug) => `/${slug}/ai/`,
    isActiveSelector: (paths) => paths.isAIPath || paths.isAgentDocsPath,
  },
  {
    id: "settings",
    label: "Settings",
    i18nKey: "iw.app_switcher.settings",
    iconNode: <SettingsIcon className="size-4" />,
    icon: SettingsIcon,
    hrefBuilder: (slug) => `/${slug}/settings/`,
    isActiveSelector: (paths) => paths.isSettingsPath,
  },
];
