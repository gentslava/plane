/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
// plane imports
import { WORKSPACE_DEFAULT_SEARCH_RESULT } from "@plane/constants";
import type { IWorkspaceSearchResults } from "@plane/types";
import { cn } from "@plane/utils";
// hooks
import { usePowerK } from "@/hooks/store/use-power-k";
import useDebounce from "@/hooks/use-debounce";
import { WorkspacePageService } from "@/services/page/workspace-page.service";
import { WorkspaceService } from "@/services/workspace.service";
// local imports
import type { TPowerKContext, TPowerKPageType } from "../../core/types";
import { PowerKModalNoSearchResultsCommand } from "./no-results-command";
import { PowerKModalSearchResults } from "./search-results";
// services init
const workspaceService = new WorkspaceService();
const workspacePageService = new WorkspacePageService();

type Props = {
  activePage: TPowerKPageType | null;
  context: TPowerKContext;
  isWorkspaceLevel: boolean;
  searchTerm: string;
  updateSearchTerm: (value: string) => void;
  handleSearchMenuClose?: () => void;
};

export function PowerKModalSearchMenu(props: Props) {
  const { activePage, context, isWorkspaceLevel, searchTerm, updateSearchTerm, handleSearchMenuClose } = props;
  // states
  const [resultsCount, setResultsCount] = useState(0);
  const [isSearching, setIsSearching] = useState(false);
  const [results, setResults] = useState<IWorkspaceSearchResults>(WORKSPACE_DEFAULT_SEARCH_RESULT);
  const debouncedSearchTerm = useDebounce(searchTerm, 500);
  // navigation
  const { workspaceSlug, projectId } = useParams();
  // store hooks
  const { togglePowerKModal } = usePowerK();

  useEffect(() => {
    if (activePage || !workspaceSlug) return;
    setIsSearching(true);

    if (debouncedSearchTerm) {
      const slug = workspaceSlug.toString();
      // Search both workspace entities and wiki pages in parallel
      Promise.all([
        workspaceService.searchWorkspace(slug, {
          ...(projectId ? { project_id: projectId.toString() } : {}),
          search: debouncedSearchTerm,
          workspace_search: !projectId ? true : isWorkspaceLevel,
        }),
        workspacePageService.fetchAll(slug, { search: debouncedSearchTerm }).catch(() => []),
      ])
        .then(([searchResults, wikiPages]) => {
          // Include workspace wiki pages in the common Pages result group. The
          // shared result mapper routes pages without project_ids to /wiki/.
          const wikiPageResults = wikiPages.map((page) => ({
            id: page.id ?? "",
            name: page.name ?? "Untitled",
            workspace__slug: slug,
          }));
          const mergedResults = {
            ...searchResults,
            results: {
              ...searchResults.results,
              page: [...(searchResults.results.page ?? []), ...wikiPageResults],
            },
          } as IWorkspaceSearchResults;
          setResults(mergedResults);
          const count = Object.values(mergedResults.results).reduce(
            (accumulator, items) => accumulator + (items?.length ?? 0),
            0
          );
          setResultsCount(count);
          return mergedResults;
        })
        .catch(() => {
          setResults(WORKSPACE_DEFAULT_SEARCH_RESULT);
          setResultsCount(0);
        })
        .finally(() => setIsSearching(false));
    } else {
      setResults(WORKSPACE_DEFAULT_SEARCH_RESULT);
      setIsSearching(false);
    }
  }, [debouncedSearchTerm, isWorkspaceLevel, projectId, workspaceSlug, activePage]);

  if (activePage) return null;

  const handleClosePalette = () => {
    handleSearchMenuClose?.();
    togglePowerKModal(false);
  };

  return (
    <>
      {searchTerm.trim() !== "" && (
        <div className="mt-4 flex items-center justify-between gap-2 px-4">
          <h5
            className={cn("text-11 text-primary", {
              "animate-pulse": isSearching,
            })}
          >
            Search results for{" "}
            <span className="font-medium">
              {'"'}
              {searchTerm}
              {'"'}
            </span>{" "}
            in {isWorkspaceLevel ? "workspace" : "project"}:
          </h5>
        </div>
      )}

      {/* Show empty state only when not loading and no results */}
      {!isSearching && resultsCount === 0 && searchTerm.trim() !== "" && debouncedSearchTerm.trim() !== "" && (
        <PowerKModalNoSearchResultsCommand
          context={context}
          searchTerm={searchTerm}
          updateSearchTerm={updateSearchTerm}
        />
      )}

      {searchTerm.trim() !== "" && <PowerKModalSearchResults closePalette={handleClosePalette} results={results} />}
    </>
  );
}
