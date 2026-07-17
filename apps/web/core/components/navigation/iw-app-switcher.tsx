/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * IW: always-visible app-switcher (3x3 grid icon) for the top navigation.
 *
 * Surfaces section navigation regardless of app-rail dock state. When the
 * rail is hidden / icon-only, this is the user's only path to switch
 * between Projects, Wiki, AI and Settings without first expanding the rail.
 *
 * Sections come from APP_SECTIONS (single source of truth shared with the
 * Power-K palette). The trailing "Dock/Undock App Rail" item flips the
 * same `useAppRailVisibility().isCollapsed` state Power-K's
 * `side_rail_toggle_visibility` command toggles.
 */

"use client";

import { Fragment } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { Menu, Transition } from "@headlessui/react";
import { LayoutGrid, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useTranslation } from "@plane/i18n";
import { CheckIcon } from "@plane/propel/icons";
import { Tooltip } from "@plane/propel/tooltip";
import { cn } from "@plane/utils";
// hooks
import { useAppRouter } from "@/hooks/use-app-router";
import { useWorkspacePaths } from "@/hooks/use-workspace-paths";
// lib
import { useAppRailVisibility } from "@/lib/app-rail";
// constants
import { APP_SECTIONS } from "@/constants/app-sections";

/** Translate `i18nKey`; if the locale is missing the key, fall back to
 *  the registry's English label rather than printing the raw key. */
const tWithFallback = (
  t: (key: string, params?: Record<string, unknown>) => string,
  key: string,
  fallback: string
): string => {
  const translated = t(key);
  return translated === key ? fallback : translated;
};

export const IWAppSwitcher = observer(function IWAppSwitcher() {
  // router
  const { workspaceSlug } = useParams();
  const router = useAppRouter();
  // app-rail dock state
  const { isCollapsed, toggleAppRail } = useAppRailVisibility();
  // route-active helpers
  const paths = useWorkspacePaths();
  // translation
  const { t } = useTranslation();

  const slug = workspaceSlug?.toString() ?? "";

  const handleSectionClick = (href: string, isActive: boolean) => {
    if (isActive) return; // no-op when already there
    router.push(href);
  };

  const handleToggleDock = () => toggleAppRail();

  return (
    <Menu as="div" className="relative flex shrink-0">
      {({ open }: { open: boolean }) => (
        <>
          <Tooltip tooltipContent={tWithFallback(t, "iw.app_switcher.tooltip", "Switch app")} position="bottom">
            <Menu.Button
              aria-label={tWithFallback(t, "iw.app_switcher.aria_label", "Open app switcher")}
              className={cn(
                "flex size-8 items-center justify-center rounded-md text-secondary transition-colors hover:bg-layer-1-hover focus:outline-none",
                {
                  "bg-layer-1": open,
                }
              )}
            >
              <LayoutGrid className="size-5" strokeWidth={1.75} />
            </Menu.Button>
          </Tooltip>
          <Transition
            as={Fragment}
            enter="transition ease-out duration-100"
            enterFrom="transform opacity-0 scale-95"
            enterTo="transform opacity-100 scale-100"
            leave="transition ease-in duration-75"
            leaveFrom="transform opacity-100 scale-100"
            leaveTo="transform opacity-0 scale-95"
          >
            <Menu.Items
              className={cn(
                "absolute top-9 left-0 z-30 mt-1 flex w-56 origin-top-left flex-col rounded-md border-[0.5px] border-strong bg-surface-1 py-1 shadow-raised-200 outline-none"
              )}
            >
              {APP_SECTIONS.map((section) => {
                const href = section.hrefBuilder(slug);
                const isActive = section.isActiveSelector(paths);
                const label = tWithFallback(t, section.i18nKey, section.label);
                return (
                  <Menu.Item key={section.id} as={Fragment}>
                    {({ active }: { active: boolean }) => (
                      <button
                        type="button"
                        onClick={() => handleSectionClick(href, isActive)}
                        className={cn(
                          "flex items-center justify-between gap-2 px-3 py-1.5 text-13 text-secondary transition-colors",
                          {
                            "bg-layer-transparent-hover": active,
                            "text-primary": isActive,
                          }
                        )}
                      >
                        <span className="flex items-center gap-2">
                          <span className="flex size-5 items-center justify-center text-secondary">
                            {section.iconNode}
                          </span>
                          <span className="text-13">{label}</span>
                        </span>
                        {isActive && <CheckIcon className="size-3.5 text-secondary" />}
                      </button>
                    )}
                  </Menu.Item>
                );
              })}
              <div className="my-1 border-t border-subtle" />
              <Menu.Item as={Fragment}>
                {({ active }: { active: boolean }) => (
                  <button
                    type="button"
                    onClick={handleToggleDock}
                    className={cn("flex items-center gap-2 px-3 py-1.5 text-13 text-secondary transition-colors", {
                      "bg-layer-transparent-hover": active,
                    })}
                  >
                    <span className="flex size-5 items-center justify-center text-secondary">
                      {isCollapsed ? (
                        <PanelLeftOpen className="size-4" strokeWidth={1.75} />
                      ) : (
                        <PanelLeftClose className="size-4" strokeWidth={1.75} />
                      )}
                    </span>
                    <span>
                      {isCollapsed
                        ? tWithFallback(t, "iw.app_switcher.dock_app_rail", "Dock App Rail")
                        : tWithFallback(t, "iw.app_switcher.undock_app_rail", "Undock App Rail")}
                    </span>
                  </button>
                )}
              </Menu.Item>
            </Menu.Items>
          </Transition>
        </>
      )}
    </Menu>
  );
});
