<!-- OVERLAY: mobile-graphql -->

# Бриф для подагентов: реализация GraphQL-мутаций мобильного приложения Plane

## Контекст

Форк Plane (Django apiserver) реализует GraphQL-шлюз для нативного мобильного приложения
(оно шлёт `POST /graphql/`). Схема снята интроспекцией Plane Cloud — это **источник истины**:
`overlay/features/mobile-graphql/reference/cloud-schema.graphql` (137 типов). Read-слой уже
готов в `apps/api/plane/graphql/resolvers.py`. Твоя задача — **мутации** своего домена.

## Движок (КРИТИЧНО — не дублируй то, что он уже делает)

Схема собирается в `apps/api/plane/graphql/schema.py`. После сборки:

- `smart_default_resolver` (resolver_utils.py) ставится на ВСЕ object-поля без явного резолвера:
  camelCase→snake_case, **FK-поле типа `ID!` → `obj.<field>_id`** (без join), модель→pk для скаляров.
  **Поэтому твоя мутация должна просто вернуть созданный/обновлённый Django-объект модели** —
  сериализация полей произойдёт сама. НЕ пиши резолверы для полей возвращаемых типов, кроме
  M2M-as-`[ID!]` (assignees/labels/modules) и вычисляемых — но обычно мутации это не требуют.
- `install_safe_query_defaults` ставит безопасные пустышки на нереализованные корневые поля.
  Поэтому реализуй ТОЛЬКО мутации своего домена; остальное не трогай.

## Конвенции

- ariadne: `from ariadne import MutationType, ObjectType`. Создай `mutation = MutationType()`
  и вешай `@mutation.field("<имяПоляИзSDL>")`. Экспортируй в конце: `BINDABLES = [mutation, ...]`.
- Резолвер: `def resolve_x(_, info, argA, argB=default):` — **имена аргументов точно как в SDL**
  (camelCase!). Объекты-инпуты (напр. `issueInput: IssueCreateInputType!`) приходят как **dict с
  camelCase-ключами** (`issueInput.get("descriptionHtml")`).
- Текущий пользователь: `from plane.graphql.context import get_user` → `user = get_user(info)`.
  Если `None` — верни безопасно (для non-null объекта можно вернуть None, но лучше не падать).
- Модели: `from plane.db.models import Issue, Project, Workspace, ...`. Поля snake_case.
- Возвращай **инстанс модели** (для object-типа) или нужный скаляр/словарь (snake_case-ключи).

## Side-effects (важно для корректного создания)

- **Issue**: `sequence_id` НЕ авто. Вычисляй: `from django.db.models import Max;
last = Issue.objects.filter(project=p).aggregate(m=Max("sequence_id"))["m"] or 0; sequence_id=last+1`.
  Состояние по умолчанию: `State.objects.filter(project=p, default=True).first()` или первый по `sequence`.
  Ставь `created_by=user, updated_by=user`. M2M (assignees/labels) — через `.set([...])` ПОСЛЕ create.
- **Project**: создание дефолтных состояний — см. эталон `apps/api/plane/app/views/project/base.py:276`
  (`State.objects.bulk_create(...)`). Добавь создателя: `ProjectMember.objects.create(project=p,
member=user, workspace=ws, role=20, is_active=True)`. Реплицируй минимально, чтобы проект был рабочим.
- Не трогай Celery/вебхуки/активность — для MVP достаточно прямого ORM-создания, чтобы объект
  появился в приложении.

## Что реализовать (твой домен — см. задачу). Для КАЖДОГО поля:

1. Найди сигнатуру в SDL (`grep "    <field>(" cloud-schema.graphql`) и input-типы (`input XInputType`).
2. Реализуй ORM-логику (create/update/delete), верни требуемый тип.
3. Удаления/булевы мутации возвращают `Boolean` → верни `True`/`False`.

## Валидация ПЕРЕД сдачей (Django локально не запускается — без БД):

1. `python3 -m py_compile <твой файл>` — синтаксис.
2. Привязки против SDL (имена полей существуют в type Mutation):
   ```
   python3 - <<'PY'
   import re
   sdl=open("overlay/features/mobile-graphql/reference/cloud-schema.graphql").read()
   mf=set(re.findall(r'\n\s*(\w+)\(', re.search(r'^type Mutation \{(.*?)\n\}',sdl,re.S|re.M).group(1)))
   res=open("<твой файл>").read()
   bad=[m.group(1) for m in re.finditer(r'@mutation\.field\("(\w+)"\)',res) if m.group(1) not in mf]
   print("BAD:", bad or "ok")
   PY
   ```
   Должно быть `ok`. Если поле не найдено — ты ошибся в имени; сверься с SDL.

## ЗАПРЕЩЕНО

- Деплоить, пушить, коммитить. Трогать другие файлы (только свой модуль). Менять `schema.graphql`,
  `resolvers.py`, `schema.py`. Импортировать несуществующие модели (сверяйся с
  `apps/api/plane/db/models/__init__.py`).

## Результат

Один файл-модуль по указанному в задаче пути, экспортирующий `BINDABLES`. В финальном сообщении —
путь к файлу, список реализованных полей, и любые поля, которые НЕ смог сделать (с причиной).
