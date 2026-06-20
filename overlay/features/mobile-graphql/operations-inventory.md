<!-- OVERLAY: mobile-graphql -->

# Инвентарь GraphQL-операций мобильного приложения Plane

Извлечено из `libapp.so` (Flutter/Dart AOT) официального приложения **Plane: Projects, Wiki, AI**
(`com.plane.so`, v2.x). Сырьё — `reference/operations-raw.graphql`, `reference/fragments-raw.graphql`.

**Всего: 188 операций (111 queries / 77 mutations).** Эндпоинт приложения: `POST {server}/graphql/`
(на Cloud — `api.plane.so/graphql/`, требует авторизации). В open-source Plane / нашей базе eyriehq
GraphQL **отсутствует** — это и есть причина бесконечного лоадера («Sorry, we are unable to get your info»).

> ⚠️ Крупные запросы (напр. `userInformationAndWorkspacesQuery`) в бинаре хранятся как AST-узлы
> (artemis codegen) — из `strings` достаётся только заголовок с переменными, а selection-set разорван.
> Точные selection-set'ы и input-типы снимаются **рантайм-перехватом** реальных POST-тел (см. PLAN.md, фаза 1).

## Точка входа (минимум для прохождения экрана «unable to get your info»)

Это первые вызовы приложения сразу после auth-флоу (по аналогии с REST `/api/users/me/...` на Cloud):

| Операция                                       | Назначение                           | Заметки                                                                        |
| ---------------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------ |
| `VersionCheckQuery($platform)`                 | гейт версии                          | возвращает `{version, minSupportedVersion, minSupportedBackendVersion}`        |
| `userInformationAndWorkspacesQuery($deviceId)` | **«your info» + список воркспейсов** | главный входной запрос                                                         |
| `WorkspacesQuery`                              | список воркспейсов                   |                                                                                |
| `WorkspaceFeatureQuery($slug)`                 | фичи воркспейса                      | `{isInitiativeEnabled}`                                                        |
| `FeatureFlagQuery($slug)`                      | фиче-флаги                           | `{collaborationCursor, editorAiOps, pageIssueEmbeds, timelineDependency, ...}` |
| `WorkspaceLicenseQuery`                        | лицензия (план)                      |                                                                                |
| `ToursQuery`                                   | онбординг-тур                        | `{tours {homeTour}}`                                                           |
| `TimezoneListQuery`                            | таймзоны                             |                                                                                |
| `ProfileMutation($autoSetTimeZone)`            | обновление профиля                   | `updateProfile{mobileTimezoneAutoSet}`                                         |
| `YourWorkQuery`                                | домашний экран «моя работа»          |                                                                                |

## Auth / User / Profile (29)

`CompleteTourMutation`, `CycleIssueUserPropertiesQuery`, `DeviceInformationMutation`, `EpicUserPropertiesQuery`,
`FeatureFlagQuery`, `InitiativeUserPropertyQuery`, `IssueUserPropertiesQuery`, `JoinUserWorkspaceInvitesMutation`,
`ModuleIssueUserPropertiesQuery`, `ProfileMutation`, `SetPasswordMutation`, `TimezoneListQuery`, `ToursQuery`,
`UpdateCycleUserPropertiesMutation`, `UpdateEpicsUserPropertiesMutation`, `UpdateInitiativeUserPropertyMutation`,
`UpdateModuleUserPropertiesMutation`, `UpdateUserPropertiesMutation`, `UserAssetMutation`, `UserDeleteMutation`,
`UserDeleteQuery`, `UserFavoriteMutation`, `UserFavoritesQuery`, `UserRecentVisitQuery`, `UserUpdate`,
`VersionCheckQuery`, `WorkspaceFeatureQuery`, `WorkspaceLicenseQuery`, `userInformationAndWorkspacesQuery`

## Workspace (32)

`CreateWorkspaceAssetMutation`, `CreateWorkspaceMutation`, `CreateWorkspacePageMutation`, `LastWorkspaceMutation`,
`PublicWorkspaceInviteMutation`, `PublicWorkspaceInviteQuery`, `PublicWorkspaceInviteV2Mutation`,
`PublicWorkspaceInviteV2Query`, `UpdateWorkspaceAssetEntityMutation`, `WorkspaceAssetMutation`, `WorkspaceAssetQuery`,
`WorkspaceInviteQuery`, `WorkspaceIssuesQuery`, `WorkspaceLabelsQuery`, `WorkspaceMembersQuery`,
`WorkspaceNestedChildArchivePagesMutation`, `WorkspaceNestedChildDeletePagesMutation`, `WorkspaceNestedParentPages`,
`WorkspacePageCommentRepliesQuery`, `WorkspacePageCommentsMutation`, `WorkspacePageCommentsQuery`,
`WorkspacePageMentionQuery`, `WorkspacePageMutation`, `WorkspacePageQuery`, `WorkspaceSlugCheckMutation`,
`WorkspaceStatesQuery`, `WorkspaceStickiesMutation`, `WorkspaceStickiesQuery`, `WorkspaceSubPagesQuery`,
`WorkspaceWorkItemMentionQuery`, `WorkspacesQuery`, `workspaceNestedChildRestorePagesMutation`

## Project (19)

`AllProjectsQuery`, `CreateProjectAssetMutation`, `InitiativeProjectsQuery`, `IsProjectPublicQuery`,
`JoinProjectMutation`, `ProjectAssetEntityMutation`, `ProjectAssetQuery`, `ProjectCoversQuery`, `ProjectMembersQuery`,
`ProjectPageCommentRepliesQuery`, `ProjectPageCommentsMutation`, `ProjectPageCommentsQuery`, `ProjectPageMentionQuery`,
`ProjectParentPagesQuery`, `ProjectQuery`, `ProjectSubPagesQuery`, `ProjectsQuery`, `TeamspaceMembersByProjectQuery`,
`UpdateProjectAssetMutation`

## WorkItem / Issue (47)

`AddEpicWorkItemRelationMutation`, `AddExistingWorkItemsMutation`, `CreateEpicWorkItemMutation`, `CreateIssue`,
`CreateIssueAttachmentMutation`, `CreateIssueLink`, `CycleIssueQuery`, `DeleteIntakeWorkItemMutation`,
`DeleteWorkItemMutation`, `EpicWorkItemsQuery`, `IntakeWorkItemActivityQuery`, `IntakeWorkItemAttachmentMutation`,
`IntakeWorkItemAttachmentQuery`, `IntakeWorkItemByWorkItemQuery`, `IntakeWorkItemCommentMutation`,
`IntakeWorkItemCommentQuery`, `IntakeWorkItemCommentReactionMutation`, `IntakeWorkItemCommentReactionQuery`,
`IntakeWorkItemCommentReplyMutation`, `IntakeWorkItemMutation`, `IntakeWorkItemQuery`, `IntakeWorkItemStatusMutation`,
`IssueActivityQuery`, `IssueAttachmentsQuery`, `IssueCommentMutation`, `IssueCommentsQuery`, `IssueCycleMutation`,
`IssueDetailsMutation`, `IssueDetailsQuery`, `IssueLinksQuery`, `IssueModuleMutation`, `IssueMutationV2`, `IssueQuery`,
`IssueRelationMutation`, `IssueRelationQuery`, `IssueShortenedMetaInfoQuery`, `IssueStatsQuery`, `ModuleIssues`,
`SubIssueMutation`, `SubIssueQuery`, `UpdateIssueAttachmentMutation`, `WorkItemCommentReactionMutation`,
`WorkItemCommentReactionQuery`, `WorkItemCommentReplyMutation`, `WorkItemPageMutation`, `WorkItemPageQuery`,
`WorkItemPageSearchQuery`

## Cycle (3)

`CycleFavoriteMutation`, `CycleUnFavoriteMutation`, `Cycles`

## Module (4)

`Module`, `ModuleFavoriteMutation`, `ModuleUnFavoriteMutation`, `Modules`

## Epic (20)

`CreateEpicLinkMutation`, `DeleteEpicMutation`, `EpicActivityQuery`, `EpicAttachmentMutation`, `EpicAttachmentsQuery`,
`EpicCommentMutation`, `EpicCommentQuery`, `EpicCommentReactionMutation`, `EpicCommentReactionQuery`, `EpicCountQuery`,
`EpicDetailsQuery`, `EpicLinksQuery`, `EpicMutation`, `EpicPageMutation`, `EpicPageQuery`, `EpicPageSearchQuery`,
`EpicQuery`, `EpicRelationQuery`, `EpicStatsQuery`, `InitiativeEpicsQuery`

## Initiative (8)

`InitiativeAttachmentsQuery`, `InitiativeInformationQuery`, `InitiativeLabelsQuery`, `InitiativeLinksQuery`,
`InitiativeQuery`, `InitiativesCountQuery`, `InitiativesQuery`, `UpdateInitiativeMutation`

## Intake (2)

`IntakeSearchQuery`, `IntakeStatsQuery`

## Page / Collection (12)

`CollectionPagesQuery`, `CollectionsQuery`, `CreateCollectionMutation`, `CreateCollectionPageMutation`,
`CreatePageMutation`, `FavoritePage`, `NestedChildArchivePagesMutation`, `NestedChildDeletePagesMutation`,
`NestedChildRestorePagesMutation`, `PageMutation`, `PageQuery`, `UnFavoritePage`

## Sticky / Favorite / Search / Notification / Home (11)

`CatchUpMarkAllAsReadMutation`, `CatchUpMarkAsReadMutation`, `CatchUpQuery`, `FavoritesQuery`, `GlobalSearchQuery`,
`LabelsQuery`, `NotificationCountQuery`, `NotificationsQuery`, `StatesQuery`, `UnsplashImagesQuery`, `YourWorkQuery`

## Прочее (1)

`UpdateMobileOnboardedMutation`
