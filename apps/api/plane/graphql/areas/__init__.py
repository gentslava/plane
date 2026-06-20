# OVERLAY: mobile-graphql — per-feature resolver modules (epics, initiatives, intake,
# pages, collections, assets, cycles/modules, issue-extras, invites/misc). Each module
# is self-contained (own QueryType/MutationType/ObjectType) and exposes BINDABLES.
#
# These are spliced into make_executable_schema BEFORE the core RESOLVERS/MUTATION_BINDABLES
# (see schema.py), so on the few fields an area re-binds that the core already implements
# (epic comment/link/create mutations, initiativesCount) the established CORE version wins;
# the area's brand-new fields/types still apply. Within this list pages_workspace is ordered
# after pages_project (shared PageType) and after epics (shared searchEpicPages) — last wins.
from .assets import BINDABLES as _assets
from .collections import BINDABLES as _collections
from .cycles_modules import BINDABLES as _cycles_modules
from .epics import BINDABLES as _epics
from .initiatives import BINDABLES as _initiatives
from .intake import BINDABLES as _intake
from .invites_misc import BINDABLES as _invites_misc
from .issue_extras import BINDABLES as _issue_extras
from .pages_project import BINDABLES as _pages_project
from .pages_workspace import BINDABLES as _pages_workspace

BINDABLES = [
    *_epics,
    *_initiatives,
    *_intake,
    *_pages_project,
    *_pages_workspace,
    *_collections,
    *_assets,
    *_cycles_modules,
    *_issue_extras,
    *_invites_misc,
]
