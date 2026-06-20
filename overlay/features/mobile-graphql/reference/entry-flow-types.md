<!-- OVERLAY: mobile-graphql -->

# Входной флоу — точные типы из Cloud SDL

```graphql
type VersionCheckType {
  version: String
  minSupportedVersion: String
  url: String
  forceUpdate: Boolean!
  minSupportedBackendVersion: String
}
```

```graphql
type UserType {
  id: ID!
  avatar: String
  coverImage: String
  dateJoined: DateTime!
  displayName: String!
  email: String!
  firstName: String!
  lastName: String!
  isActive: Boolean!
  isBot: Boolean!
  isEmailVerified: Boolean!
  userTimezone: String!
  username: String!
  isPasswordAutoset: Boolean!
  lastLoginMedium: String!
  avatarUrl: String
  coverImageUrl: String
}
```

```graphql
type ProfileType {
  id: ID!
  user: ID!
  theme: JSON!
  isTourCompleted: Boolean!
  onboardingStep: JSON!
  useCase: String
  role: String
  isOnboarded: Boolean!
  lastWorkspaceId: ID
  billingAddressCountry: JSON!
  billingAddress: String
  hasBillingAddress: Boolean!
  companyName: String!
  mobileTimezoneAutoSet: Boolean!
  isMobileOnboarded: Boolean!
}
```

```graphql
type WorkspaceType {
  id: UUID!
  name: String!
  slug: String!
  logo: String
  owner: ID!
  organizationSize: String
  logoUrl: String
  deletedAt: DateTime
  role: Int
}
```

```graphql
type WorkspaceMemberType {
  id: ID!
  member: UserType!
  role: Int!
  isActive: Boolean!
}
```

```graphql
type TourType {
  homeTour: Boolean!
}
```

```graphql
type FeatureFlagType {
  oidcSamlAuth: Boolean!
  homeAdvanced: Boolean!
  inboxStacking: Boolean!
  workspaceActiveCycles: Boolean!
  customers: Boolean!
  initiatives: Boolean!
  teamspaces: Boolean!
  epics: Boolean!
  projectTemplates: Boolean!
  pageTemplates: Boolean!
  projectTemplatesPublish: Boolean!
  viewAccessPrivate: Boolean!
  viewLock: Boolean!
  viewPublish: Boolean!
  projectOverview: Boolean!
  projectGrouping: Boolean!
  projectUpdates: Boolean!
  bulkOpsOne: Boolean!
  bulkOpsPro: Boolean!
  cycleProgressCharts: Boolean!
  issueTypes: Boolean!
  issueWorklog: Boolean!
  workitemTemplates: Boolean!
  workItemConversion: Boolean!
  copyWorkItem: Boolean!
  estimateWithTime: Boolean!
  timeEstimates: Boolean!
  workflows: Boolean!
  intakeSettings: Boolean!
  intakeEmail: Boolean!
  intakeForm: Boolean!
  linkPages: Boolean!
  collaborationCursor: Boolean!
  editorAiOps: Boolean!
  pageIssueEmbeds: Boolean!
  pagePublish: Boolean!
  movePages: Boolean!
  nestedPages: Boolean!
  workspacePages: Boolean!
  sharedPages: Boolean!
  editorAttachments: Boolean!
  editorMathematics: Boolean!
  editorExternalEmbeds: Boolean!
  pageComments: Boolean!
  editorAdvancedMentions: Boolean!
  editorCopyBlockLink: Boolean!
  editorVideoAttachments: Boolean!
  silo: Boolean!
  siloImporters: Boolean!
  flatfileImporter: Boolean!
  jiraImporter: Boolean!
  jiraIssueTypesImporter: Boolean!
  jiraServerImporter: Boolean!
  jiraServerIssueTypesImporter: Boolean!
  linearImporter: Boolean!
  linearTeamsImporter: Boolean!
  asanaImporter: Boolean!
  asanaIssuePropertiesImporter: Boolean!
  clickupImporter: Boolean!
  clickupIssuePropertiesImporter: Boolean!
  notionImporter: Boolean!
  siloIntegrations: Boolean!
  githubIntegration: Boolean!
  gitlabIntegration: Boolean!
  slackIntegration: Boolean!
  fileSizeLimitPro: Boolean!
  timelineDependency: Boolean!
  piChat: Boolean!
  piDedupe: Boolean!
  piConverse: Boolean!
  piFileUploads: Boolean!
  aiChat: Boolean!
  aiDedupe: Boolean!
  aiConverse: Boolean!
  aiFileUploads: Boolean!
  aiPagesBlocks: Boolean!
  aiPagesSummary: Boolean!
  advancedSearch: Boolean!
}
```

```graphql
type WorkspaceFeatureType {
  isProjectGroupingEnabled: Boolean!
  isInitiativeEnabled: Boolean!
  isTeamsEnabled: Boolean!
  isCustomerEnabled: Boolean!
}
```

```graphql
type WorkspaceLicenseType {
  isCancelled: Boolean
  purchasedSeats: Int
  currentPeriodEndDate: String
  interval: String
  product: String
  isOfflinePayment: Boolean
  trialEndDate: String
  hasActivatedFreeTrial: Boolean
  hasAddedPaymentMethod: Boolean
  subscription: String
  isSelfManaged: Boolean
  isOnTrial: Boolean
  isTrialAllowed: Boolean
  remainingTrialDays: Int
  hasUpgraded: Boolean
  showPaymentButton: Boolean
  showTrialBanner: Boolean
  freeSeats: Int
  occupiedSeats: Int
  showSeatsBanner: Boolean
  currentPeriodStartDate: String
  isTrialEnded: Boolean
  billableMembers: Int
  isFreeMemberCountExceeded: Boolean
  canDeleteWorkspace: Boolean
  showVerificationFailedBanner: Boolean
}
```

```graphql
type TimezoneListType {
  value: String!
  query: String!
  label: String!
}
```

```graphql
type WorkspaceYourWorkType {
  projects: Int!
  issues: Int!
  pages: Int!
}
```
