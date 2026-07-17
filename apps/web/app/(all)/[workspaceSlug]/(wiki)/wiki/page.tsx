/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { WikiIcon } from "@plane/propel/icons";

function WikiHomePage() {
  return (
    <div className="flex size-full flex-col items-center justify-center gap-4 text-center">
      <div className="flex size-16 items-center justify-center rounded-xl bg-layer-transparent-hover">
        <WikiIcon className="size-8 text-tertiary" />
      </div>
      <div className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold text-primary">Workspace Wiki</h2>
        <p className="text-sm max-w-md text-secondary">
          Your workspace knowledge base. Create and organize pages to share information with your team.
        </p>
      </div>
    </div>
  );
}

export default observer(WikiHomePage);
