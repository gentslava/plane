# Workflow Approval — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UI для управляемых переходов статусов: настройки workflow на проект (admin) + превентивная блокировка drag-n-drop на доске + реактивная модалка на 403.

**Architecture:** Заполняем существующие CE-заглушки `@/plane-web/components/workflow` (которые core-Kanban/List уже вызывает) реальной логикой поверх нового MobX-стора, читающего наш backend API; добавляем admin-раздел настроек карточным списком. Backend (ветка `feature/workflow-approval`) уже готов и протестирован — фронт только потребляет его.

**Tech Stack:** Next.js (app-router), MobX (mobx-react `observer`), TypeScript, `@plane/propel/toast`, `@plane/i18n`, `@atlaskit/pragmatic-drag-and-drop`. Пакеты: `apps/web` (фронт), `packages/constants`, `packages/i18n`, `packages/types`.

**ВАЖНО — нет юнит-тестов на фронте.** Верификация каждой задачи:

- `pnpm --filter web typecheck` (или `cd apps/web && pnpm typecheck`) — ноль ошибок типов в затронутых файлах.
- `pnpm --filter web lint` на затронутых файлах — чисто.
- Browser-верификация ключевых сценариев на локальном dev-стенде (web+api) через chrome-devtools/playwright MCP — описана в Task 6. **Точные команды typecheck/lint сверь в `apps/web/package.json` scripts на первом шаге Task 1** (плейсхолдер-команды ниже приведены по соглашению Plane; подставь фактические).

**Источник истины:** спека `docs/superpowers/specs/2026-06-25-workflow-approval-design.md` §8.2; backend API — `apps/api/plane/app/views/workflow.py` + контракт ошибок из `apps/api/plane/workflow/exceptions.py`.

---

## Backend API-контракт (что потребляет фронт)

Все под `/api/workspaces/{slug}/projects/{projectId}/`:

- `GET workflow/` → `{ id|null, project, workspace|null, is_enabled }`; `PATCH workflow/ {is_enabled}` → 200.
- `GET workflow-states/` → `[{ id, state, allow_issue_creation }]`; `PATCH workflow-states/{stateId}/ {allow_issue_creation}` → 200.
- `GET workflow-transitions/` → `[{ id, state, transition_state, approvers: string[] }]`; `POST workflow-transitions/ {state, transition_state}` → 201; `DELETE workflow-transitions/{id}/` → 204.
- `POST workflow-transitions/{transitionId}/approvers/ {approver}` → 201 (400 если не активный участник); `DELETE workflow-transitions/{transitionId}/approvers/{id}/` → 204.
- Только **admin** (иначе 403). Контракт ошибок enforcement: 403 `{error_code:"WORKFLOW_TRANSITION_BLOCKED", message, from_state, to_state, allowed_reviewers:string[]}`; 400 `{error_code:"WORKFLOW_CREATION_BLOCKED", message, state}`.

---

## Файловая структура

| Файл                                                                                               | Ответственность                                                        | Действие |
| -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | -------- |
| `packages/types/src/workflow.ts`                                                                   | Типы `TProjectWorkflow`, `TWorkflowTransition`, `TWorkflowStateConfig` | Create   |
| `packages/types/src/index.ts`                                                                      | Реэкспорт                                                              | Modify   |
| `apps/web/core/services/project/workflow.service.ts`                                               | HTTP-обёртка над backend API                                           | Create   |
| `apps/web/core/store/workflow.store.ts`                                                            | MobX-стор конфигурации + проверка переходов                            | Create   |
| `apps/web/core/store/root.store.ts`                                                                | Регистрация стора                                                      | Modify   |
| `apps/web/core/hooks/store/use-workflow.ts`                                                        | Хук доступа к стору                                                    | Create   |
| `apps/web/ce/components/workflow/use-workflow-drag-n-drop.ts`                                      | Заполнить заглушку: реальная блокировка drag                           | Modify   |
| `apps/web/ce/components/workflow/workflow-disabled-message.tsx`                                    | Подсказка на запрещённой колонке (опц.)                                | Modify   |
| `apps/web/ce/components/workflow/workflow-blocker-modal.tsx`                                       | Реактивная модалка на 403                                              | Create   |
| `apps/web/ce/components/projects/settings/workflows/root.tsx`                                      | Корень раздела настроек (карточный список)                             | Create   |
| `apps/web/ce/components/projects/settings/workflows/workflow-state-card.tsx`                       | Карточка статуса: переходы + исполнители                               | Create   |
| `apps/web/ce/components/projects/settings/workflows/transition-row.tsx`                            | Строка перехода + member-picker                                        | Create   |
| `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/workflows/page.tsx`   | Страница раздела                                                       | Create   |
| `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/workflows/header.tsx` | Header                                                                 | Create   |
| `packages/constants/src/settings/project.ts`                                                       | Регистрация пункта меню настроек                                       | Modify   |
| `packages/i18n/src/locales/en/project-settings.json` (+ др. локали)                                | Ключи i18n                                                             | Modify   |

**Scope (MVP, по §8.2):** настройки карточным списком (не canvas), превентивная блокировка Kanban, реактивная модалка. **Вне MVP:** визуальный граф-редактор, workspace-уровень, аудит-лог UI.

---

## Task 1: Типы + Service + Store + хук

**Files:**

- Create: `packages/types/src/workflow.ts`, `apps/web/core/services/project/workflow.service.ts`, `apps/web/core/store/workflow.store.ts`, `apps/web/core/hooks/store/use-workflow.ts`
- Modify: `packages/types/src/index.ts`, `apps/web/core/store/root.store.ts`

- [ ] **Step 1: Сверить инфраструктуру.** Прочитай `apps/web/package.json` scripts — зафиксируй точные команды typecheck/lint. Прочитай `apps/web/core/services/project/project-state.service.ts` (паттерн `APIService`), `apps/web/core/store/state.store.ts` (паттерн стора), `apps/web/core/store/root.store.ts` (регистрация), `apps/web/core/hooks/store/use-project-state.ts` (хук). Следуй этим паттернам буквально.

- [ ] **Step 2: Типы.** Create `packages/types/src/workflow.ts`:

```ts
export type TProjectWorkflow = {
  id: string | null;
  project?: string;
  workspace?: string | null;
  is_enabled: boolean;
};

export type TWorkflowStateConfig = {
  id: string;
  state: string;
  allow_issue_creation: boolean;
};

export type TWorkflowTransition = {
  id: string;
  state: string; // from-state id
  transition_state: string; // to-state id
  approvers: string[]; // user ids; empty = any project member
};
```

Modify `packages/types/src/index.ts` — добавь `export * from "./workflow";` (сверь стиль реэкспорта в файле).

- [ ] **Step 3: Service.** Create `apps/web/core/services/project/workflow.service.ts` по образцу `project-state.service.ts` (lic-шапка как в нём). Методы (точные сигнатуры `get/post/patch/delete` сверь по APIService):

```ts
import { APIService } from "@/services/api.service";
import { API_BASE_URL } from "@plane/constants";
import type { TProjectWorkflow, TWorkflowStateConfig, TWorkflowTransition } from "@plane/types";

export class WorkflowService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  base(slug: string, p: string) {
    return `/api/workspaces/${slug}/projects/${p}`;
  }

  getWorkflow(slug: string, p: string): Promise<TProjectWorkflow> {
    return this.get(`${this.base(slug, p)}/workflow/`)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  updateWorkflow(slug: string, p: string, data: Partial<TProjectWorkflow>): Promise<TProjectWorkflow> {
    return this.patch(`${this.base(slug, p)}/workflow/`, data)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  getStateConfigs(slug: string, p: string): Promise<TWorkflowStateConfig[]> {
    return this.get(`${this.base(slug, p)}/workflow-states/`)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  updateStateConfig(
    slug: string,
    p: string,
    stateId: string,
    data: Partial<TWorkflowStateConfig>
  ): Promise<TWorkflowStateConfig> {
    return this.patch(`${this.base(slug, p)}/workflow-states/${stateId}/`, data)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  getTransitions(slug: string, p: string): Promise<TWorkflowTransition[]> {
    return this.get(`${this.base(slug, p)}/workflow-transitions/`)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  createTransition(
    slug: string,
    p: string,
    data: { state: string; transition_state: string }
  ): Promise<TWorkflowTransition> {
    return this.post(`${this.base(slug, p)}/workflow-transitions/`, data)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  deleteTransition(slug: string, p: string, id: string): Promise<void> {
    return this.delete(`${this.base(slug, p)}/workflow-transitions/${id}/`)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  addApprover(slug: string, p: string, transitionId: string, approver: string): Promise<{ id: string }> {
    return this.post(`${this.base(slug, p)}/workflow-transitions/${transitionId}/approvers/`, { approver })
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
  removeApprover(slug: string, p: string, transitionId: string, id: string): Promise<void> {
    return this.delete(`${this.base(slug, p)}/workflow-transitions/${transitionId}/approvers/${id}/`)
      .then((r) => r?.data)
      .catch((e) => {
        throw e?.response?.data;
      });
  }
}
```

- [ ] **Step 4: Store.** Create `apps/web/core/store/workflow.store.ts` по образцу `state.store.ts`. Observable: `workflowByProject: Record<projectId, TProjectWorkflow>`, `transitionsByProject: Record<projectId, TWorkflowTransition[]>`, `stateConfigsByProject: Record<projectId, TWorkflowStateConfig[]>`, `fetchedMap`. Actions: `fetchWorkflow`, `fetchTransitions`, `fetchStateConfigs`, `toggleWorkflow`, `createTransition`, `deleteTransition`, `addApprover`, `removeApprover`, `updateStateConfig` — каждый зовёт service + `runInAction(set(...))`, с откатом на ошибке (паттерн `state.store.ts`). Плюс **computed-функции для доски**:

```ts
// Разрешён ли переход from→to в проекте (для drag-блокировки).
isTransitionAllowed = computedFn(
  (projectId: string, fromStateId: string, toStateId: string, userId: string): boolean => {
    const wf = this.workflowByProject[projectId];
    if (!wf?.is_enabled) return true; // workflow выключен → всё разрешено
    if (fromStateId === toStateId) return true; // не переход
    const t = (this.transitionsByProject[projectId] ?? []).find(
      (x) => x.state === fromStateId && x.transition_state === toStateId
    );
    if (!t) return false; // нет записи → запрещено
    return t.approvers.length === 0 || t.approvers.includes(userId); // пусто = любой
  }
);

getAllowedReviewers = computedFn((projectId, fromStateId, toStateId): string[] => {
  const t = (this.transitionsByProject[projectId] ?? []).find(
    (x) => x.state === fromStateId && x.transition_state === toStateId
  );
  return t?.approvers ?? [];
});
```

(`computedFn` из `mobx-utils` — сверь, как `state.store.ts` делает параметризованные computed; если использует обычные методы — следуй тому же.)

- [ ] **Step 5: Регистрация + хук.** Modify `apps/web/core/store/root.store.ts` — добавь поле `workflow: IWorkflowStore` и инициализацию `new WorkflowStore(this)` (по образцу `this.state = new StateStore(...)`). Create `apps/web/core/hooks/store/use-workflow.ts` по образцу `use-project-state.ts` (возвращает `context.workflow`).

- [ ] **Step 6: Верификация.** Run: `cd apps/web && pnpm typecheck` (или фактическая команда) — ноль новых ошибок. `pnpm lint` на затронутых файлах — чисто.

- [ ] **Step 7: Commit.**

```bash
git add packages/types/src/workflow.ts packages/types/src/index.ts \
        apps/web/core/services/project/workflow.service.ts apps/web/core/store/workflow.store.ts \
        apps/web/core/store/root.store.ts apps/web/core/hooks/store/use-workflow.ts
git commit -m "feat(workflow-ui): types, service, store and hook"
```

---

## Task 2: Заполнить CE-заглушку drag-n-drop блокировки

**Files:**

- Modify: `apps/web/ce/components/workflow/use-workflow-drag-n-drop.ts`
- Read: `apps/web/core/components/issues/issue-layouts/kanban/kanban-group.tsx` (как используется хук)

- [ ] **Step 1: Сверить контракт заглушки.** Прочитай ТЕКУЩИЙ `apps/web/ce/components/workflow/use-workflow-drag-n-drop.ts` — точную сигнатуру возвращаемого объекта (`isWorkflowDropDisabled`, `getIsWorkflowWorkItemCreationDisabled`, `handleWorkFlowState`, `workflowDisabledSource`, …) и типы аргументов. Прочитай `kanban-group.tsx` (строки вокруг `useWorkFlowFDragNDrop`, `handleWorkFlowState`, `isWorkflowDropDisabled`, `dropErrorMessage`) — как core их потребляет. **Сохрани сигнатуру 1-в-1**, только наполни тело.

- [ ] **Step 2: Реализовать логику.** Заполни хук: на `handleWorkFlowState(sourceGroupId, destGroupId, ...)` запомни пару (from=sourceGroupId, to=destGroupId) при groupBy === "state"; вычисли `isWorkflowDropDisabled = !workflowStore.isTransitionAllowed(projectId, from, to, currentUserId)` и `dropErrorMessage = t("project_settings.workflow.transition_blocked")`. Используй `useWorkflow()` (Task 1), `useParams()` для projectId, `useUser()` для currentUserId. Активна логика только при `groupBy === "state"` (иначе верни дефолт-разрешено, как заглушка). Подгрузи workflow/transitions проекта (если не загружены) через `useEffect`/`fetch*` в подходящем месте (или в layout-рендере доски).

- [ ] **Step 3: Подсказка (опц.).** Modify `apps/web/ce/components/workflow/workflow-disabled-message.tsx` — вместо пустого фрагмента покажи короткий текст «Переход запрещён правилами workflow», когда колонка-цель недоступна (если core рендерит этот компонент в запрещённой колонке — сверь по grep использования).

- [ ] **Step 4: Верификация.** typecheck + lint. Затем browser (Task 6): включить workflow + один разрешённый переход, проверить что drag в недозволенную колонку блокируется (toast/визуально), а в разрешённую — проходит.

- [ ] **Step 5: Commit.**

```bash
git add apps/web/ce/components/workflow/use-workflow-drag-n-drop.ts apps/web/ce/components/workflow/workflow-disabled-message.tsx
git commit -m "feat(workflow-ui): enforce transitions in board drag-n-drop"
```

---

## Task 3: Раздел настроек workflow (admin)

**Files:**

- Create: `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/projects/[projectId]/workflows/page.tsx`, `.../workflows/header.tsx`, `apps/web/ce/components/projects/settings/workflows/{root,workflow-state-card,transition-row}.tsx`
- Modify: `packages/constants/src/settings/project.ts`, `packages/i18n/src/locales/en/project-settings.json` (+ др. локали)

- [ ] **Step 1: Сверить паттерн.** Прочитай существующий раздел-образец `.../settings/projects/[projectId]/states/page.tsx` + `header.tsx` + `apps/web/core/components/project-states/root.tsx`, и `packages/constants/src/settings/project.ts` (`PROJECT_SETTINGS`, `GROUPED_PROJECT_SETTINGS`). Следуй им.

- [ ] **Step 2: Регистрация меню.** Modify `packages/constants/src/settings/project.ts`: добавь в `PROJECT_SETTINGS` ключ `workflows` (`href: "/workflows"`, `access: [ADMIN]` — только админ, т.к. конфиг admin-only; i18n_label `project_settings.workflow.label`; highlight по `/workflows/`), и включи его в `GROUPED_PROJECT_SETTINGS` (категория `WORK_STRUCTURE` или `EXECUTION` — выбери по смыслу, сверь enum). **Доступ строго ADMIN** (backend всё равно вернёт 403 не-админу).

- [ ] **Step 3: i18n.** Modify `packages/i18n/src/locales/en/project-settings.json` — добавь блок `project_settings.workflow`: `label`, `heading`, `description`, `enable`, `transition_blocked`, `allowed_reviewers`, `add_transition`, `approvers`, `allow_creation`, `any_member`. Повтори ключи в остальных локалях (минимум `ru`, остальные — английский фолбэк; сверь набор локалей в `packages/i18n/src/locales/`). Используй skill `translate`, если он применим в этом репо.

- [ ] **Step 4: Page + header.** Create `workflows/page.tsx` и `header.tsx` по образцу `states/`: `observer`, `SettingsContentWrapper`, `SettingsHeading` (заголовки из i18n), гейт по admin-permission (`allowPermissions([ADMIN], PROJECT)`), рендер `<WorkflowSettingsRoot workspaceSlug projectId />`.

- [ ] **Step 5: Root + карточки.** Create `ce/components/projects/settings/workflows/root.tsx`: `observer`, на маунте `fetchWorkflow/fetchStateConfigs/fetchTransitions` + `fetchProjectStates`; сверху — тумблер `is_enabled` (`toggleWorkflow`); ниже — список статусов проекта, на каждый `WorkflowStateCard`. Create `workflow-state-card.tsx`: показывает статус, его `allow_issue_creation` тумблер (`updateStateConfig`), и список разрешённых исходящих переходов (`TransitionRow` на каждый существующий + кнопка «добавить переход» → выбор целевого статуса → `createTransition`). Create `transition-row.tsx`: цель перехода + member-picker исполнителей (компонент `apps/web/core/components/project/member-select.tsx`, multi — сверь, поддерживает ли он мультивыбор; если нет — добавляй approver'ов по одному через `addApprover`/`removeApprover`), удаление перехода (`deleteTransition`). Все мутации — с `setToast` на успех/ошибку (паттерн из разведки).

- [ ] **Step 6: Верификация.** typecheck + lint. Browser (Task 6): зайти в Settings проекта → раздел Workflow виден только админу; включить тумблер; добавить переход backlog→done; назначить исполнителя; убедиться, что данные сохраняются (network 200/201) и переживают перезагрузку.

- [ ] **Step 7: Commit.**

```bash
git add apps/web/app/'(all)'/'[workspaceSlug]'/'(settings)'/settings/projects/'[projectId]'/workflows/ \
        apps/web/ce/components/projects/settings/workflows/ \
        packages/constants/src/settings/project.ts packages/i18n/src/locales/
git commit -m "feat(workflow-ui): admin settings section (toggle, transitions, approvers)"
```

---

## Task 4: Реактивная модалка на 403 (не-drag смена статуса)

**Files:**

- Create: `apps/web/ce/components/workflow/workflow-blocker-modal.tsx`
- Read: где меняется статус из деталей issue (state-dropdown) — `apps/web/core/components/dropdowns/state/base.tsx` и обработчик update issue

- [ ] **Step 1: Сверить путь не-drag смены статуса.** Прочитай `apps/web/core/components/dropdowns/state/base.tsx` (StateDropdown) и найди, где выбор статуса вызывает `updateIssue({state_id})` и как обрабатывается ошибка. Это путь, где drag-блокировка не применяется и сервер вернёт 403 `WORKFLOW_TRANSITION_BLOCKED`.

- [ ] **Step 2: Модалка.** Create `workflow-blocker-modal.tsx`: контролируемая модалка (`isOpen`, `onClose`, `allowedReviewers: string[]`, `fromState`, `toState`), показывает сообщение «переход запрещён» и список разрешённых исполнителей (резолв имён через `useMember`). Используй `ModalCore`/`EModalWidth` из `@plane/ui` (сверь доступный модальный примитив).

- [ ] **Step 3: Подключить ловлю 403.** В обработчике смены статуса (или в store-action `updateIssue`) добавь `catch`: если `error?.error_code === "WORKFLOW_TRANSITION_BLOCKED"` → открыть модалку с `error.allowed_reviewers`/`from_state`/`to_state` (вместо обычного error-toast). Иначе — обычный toast. **Минимально**: оберни только пользовательский путь смены статуса из деталей/дропдауна; не трогай несвязанные update-потоки.

- [ ] **Step 4: Верификация.** typecheck + lint. Browser: при включённом workflow сменить статус issue из дропдауна на недозволенный → появляется модалка (не generic-toast), статус не меняется.

- [ ] **Step 5: Commit.**

```bash
git add apps/web/ce/components/workflow/workflow-blocker-modal.tsx <изменённые dropdown/handler файлы>
git commit -m "feat(workflow-ui): reactive blocker modal on 403 transition"
```

---

## Task 5: Финальная верификация на dev-стенде + индикаторы (опц.)

**Files:** опционально `apps/web/ce/components/workflow/state-option.tsx`, `workflow-disabled-overlay.tsx` (визуальные индикаторы на статусах/оверлей при drag).

- [ ] **Step 1: Поднять dev-стенд.** Backend: `docker compose -f docker-compose-local.yml up -d` (или существующий способ запуска api+web; сверь README/Makefile). Web: `cd apps/web && pnpm dev`. Залогиниться, создать проект, статусы.

- [ ] **Step 2: E2E-сценарии (browser MCP).** Прогнать вручную/через chrome-devtools MCP:
  1. Admin видит раздел Workflow; member — нет.
  2. Включить workflow, добавить переход Backlog→In Progress, назначить исполнителя.
  3. На Kanban: drag из Backlog в In Progress (разрешённый) — проходит; drag в Done (нет перехода) — блокируется с подсказкой.
  4. Смена статуса из дропдауна на недозволенный → модалка с исполнителями.
  5. Создание issue в статусе с выключенным созданием → ошибка.
  6. Выключить workflow → всё снова свободно.

- [ ] **Step 3: Индикаторы (опц., если время есть).** Заполнить `state-option.tsx`/`workflow-disabled-overlay.tsx` визуальными подсказками (иконка «workflow активен» на статусе, оверлей запрещённых колонок при drag). Сверить, как core их рендерит, сохранить сигнатуры.

- [ ] **Step 4: Финальный typecheck/lint/build.** `cd apps/web && pnpm typecheck && pnpm lint && pnpm build` — всё зелёное.

- [ ] **Step 5: Commit.**

```bash
git add apps/web/ce/components/workflow/
git commit -m "feat(workflow-ui): board indicators and final verification"
```

---

## Self-Review (выполнено при написании плана)

**Покрытие спеки §8.2:**

- «Настройки карточным списком» → Task 3 ✓
- «Превентивная блокировка Kanban (fail-closed)» → Task 2 (заполнение CE-заглушки, которую core уже зовёт) ✓
- «Реактивная модалка на 403» → Task 4 ✓
- Слои store/service/hook/ce-компоненты → Task 1 + структура ✓
- i18n → Task 3 Step 3 ✓
- Вне MVP (canvas, workspace-уровень, аудит UI) — явно исключены ✓

**Места, требующие сверки с живым кодом при реализации (помечены в шагах):** точные команды typecheck/lint; сигнатура CE-заглушки `useWorkFlowFDragNDrop` и потребление в `kanban-group.tsx`; мультивыбор у `member-select`; модальный примитив `@plane/ui`; путь смены статуса из дропдауна; категория настроек и enum в constants; набор локалей. Все локальны, архитектуру не меняют.

**Согласованность имён:** `WorkflowService`/`WorkflowStore`/`useWorkflow`; типы `TProjectWorkflow`/`TWorkflowTransition`/`TWorkflowStateConfig`; методы service совпадают с вызовами store; `is_enabled`, `transition_state`, `approvers` — как в backend-контракте.

**Адаптация под отсутствие тестов:** вместо TDD — typecheck+lint gate на каждой задаче + browser-сценарии (Task 5) как приёмочная проверка. Это сознательное отклонение от стандартного TDD-формата плана, обусловленное отсутствием тестовой инфраструктуры на фронте Plane.
