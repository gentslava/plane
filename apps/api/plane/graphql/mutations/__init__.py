# OVERLAY: mobile-graphql — aggregated mutation bindables (one module per domain).
from .planning import BINDABLES as _planning
from .work_items import BINDABLES as _work_items
from .workspace import BINDABLES as _workspace

BINDABLES = [*_work_items, *_workspace, *_planning]
