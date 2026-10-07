# Keystone — MVC Refactor Prompt

Give this entire file to a fresh Claude Code session. It is self-contained.

---

## 0. What you are doing and why

Refactor the Keystone codebase from a **domain-driven modular monolith** (code grouped
by domain/feature) into a **layer-first MVC architecture** (code grouped by role:
Model, View, Controller). The backend is FastAPI + SQLAlchemy async + arq; the
frontend is Vite + React 18 + TypeScript.

The app is a private, source-grounded company knowledge-base (NotebookLM-style),
multi-tenant. Every DB query is scoped by `org_id`. Do not change any behaviour,
schema, or test logic — this is a pure structural/import refactor.

---

## 1. Current structure (what exists right now)

```
keystone_project/
  backend/
    main.py                    # FastAPI app factory; mounts 6 routers
    worker.py                  # arq worker entry; 3 job functions
    app/
      platform/                # cross-cutting infra — stays mostly unchanged
        config.py              # Settings (pydantic-settings singleton)
        context.py             # TenantContext frozen dataclass
        db.py                  # Base, engine, sessionmaker, tenant_session()
        http.py                # register_exception_handlers()
        logging.py             # get_logger(), configure_logging()
        repository.py          # BaseRepository[ModelT] + _scoped()
        storage.py             # ObjectStore protocol, R2/Local impls, get_object_store()
        queue.py               # JobQueue protocol, ArqJobQueue, get_job_queue()
        seams/                 # Parser/Embedder/LLM protocols, fakes, real impls, factory
          __init__.py
          protocols.py
          types.py
          fakes.py
          real_parser.py
          real_llm.py
          factory.py
      identity/
        models.py              # Organization, User (ORM)
        schemas.py             # SignupRequest, LoginRequest, InviteRequest,
                               #   RoleChangeRequest, TokenResponse, UserOut,
                               #   OrgChoice, LoginAmbiguousResponse
        repository.py          # OrganizationRepository, AuthRepository, UserRepository
        service.py             # AuthService → auth_service singleton
        router.py              # /auth endpoints (8 routes)
        deps.py                # current_user(), get_ctx(), require_admin() FastAPI deps
        tokens.py              # issue/decode access+refresh tokens
        passwords.py           # hash_password(), verify_password()
        exceptions.py          # AuthError hierarchy
        constants.py           # ROLE_OWNER, ADMIN_ROLES, ROLES
      documents/
        models.py              # Folder, Tag, Document, DocumentTag (ORM)
        schemas.py             # FolderCreate/Out/Rename/Move, TagCreate/Out, DocumentOut
        status.py              # DocumentStatus StrEnum
        exceptions.py          # DocumentsError hierarchy
        router.py              # /documents endpoints (13 routes)
        repository/            # subpackage (was >200 lines)
          __init__.py
          folders.py           # FolderRepository
          tags.py              # TagRepository, DocumentTagRepository
          documents.py         # DocumentRepository
        service/               # subpackage (was >200 lines)
          __init__.py          # DocumentsService → documents_service singleton
          folders.py           # create/list/get/delete/rename/move folder
          tags.py              # create/list/delete tag; tag/untag document
          documents.py         # upload/list/get/status-transitions
      ingestion/
        models.py              # Section, Chunk, Embedding (ORM)
        schemas.py             # ChunkHit, ChunkRecord
        repository.py          # SectionRepository, ChunkRepository, EmbeddingRepository
        tasks.py               # 3 arq job functions
        router.py              # /ingestion endpoints (3 routes; manual triggers)
        service/               # subpackage
          __init__.py          # IngestionService → ingestion_service singleton
          parsing.py
          structuring.py
          embedding.py
          search.py
      knowledge/
        models.py              # Notebook → knowledge_bases, NotebookDocument
        schemas.py             # NotebookCreate, NotebookUpdate, NotebookOut
        exceptions.py          # KnowledgeError, NotebookNotFound
        repository.py          # NotebookRepository, NotebookDocumentRepository
        service.py             # KnowledgeService → knowledge_service singleton
        router.py              # /notebooks endpoints (8 routes)
      retrieval/
        schemas.py             # RetrievalSearchRequest, ContextBlock, RetrievalSearchResponse
        service.py             # RetrievalService, resolve_allowed_documents, assemble_context
        router.py              # /retrieval/search (1 route)
      chat/
        models.py              # Conversation, Message (ORM)
        schemas.py             # ChatRequest, ResolvedCitation, ChatResponse
        exceptions.py          # GenerationFailed
        repository.py          # ConversationRepository, MessageRepository
        service.py             # ChatService (304 lines); build_messages, generate_answer,
                               #   call_llm_with_retry, parse_citation_markers,
                               #   resolve_citations, ask, stream_ask, _persist
        router.py              # /chat/ask, /chat/stream (2 routes)
    migrations/
      env.py                   # Alembic env; imports all models for metadata
      versions/
        0001_baseline.py       # organizations, users
        0002_rls_scaffolding.py
        0003_auth_password_hash.py
        0004_folders_tags.py
        0005_document_upload_dedupe.py
        0006_sections_chunks.py
        0007_embeddings.py
        0008_notebooks.py
        0009_conversations_messages.py
        0010_folder_root_uniqueness.py
    tests/
      conftest.py
      test_config_smoke.py
      test_layout_smoke.py
      test_db_smoke.py
      test_migration_smoke.py
      test_metadata_smoke.py
      test_repository_scoping.py
      test_tenant_session.py
      test_tenant_isolation.py
      test_seams.py
      test_storage.py
      test_auth.py
      test_documents.py
      test_folder_moves.py
      test_ingestion.py
      test_ingestion_dispatch.py
      test_knowledge.py
      test_retrieval.py
      test_chat.py
      test_real_parser_integration.py

  frontend/
    src/
      main.tsx
      App.tsx
      index.css
      vite-env.d.ts
      lib/
        api.ts                 # ALL types + fetch util + 4 API namespaces
                               #   (authApi, documentsApi, notebooksApi, chatApi)
        auth.tsx               # AuthProvider, useAuth hook
      components/
        ProtectedRoute.tsx
        StatusBadge.tsx
      features/
        app/
          AppShell.tsx
          Sidebar.tsx
          HomePage.tsx
        auth/
          LoginPage.tsx
          SignupPage.tsx
        documents/
          DocumentsPage.tsx
          FolderTree.tsx
          FolderTree.test.tsx
          DocumentList.tsx
          DocumentList.test.tsx
        notebooks/
          NotebookList.tsx
          NotebookList.test.tsx
          NotebookPage.tsx
          NotebookPage.test.tsx
        chat/
          ChatPanel.tsx
          ChatPanel.test.tsx
          CitationPanel.tsx
        users/
          UsersPage.tsx        # (exists — admin role table)
      test/
        setup.ts
```

---

## 2. Target MVC structure

### 2a. Backend — target

```
backend/
  main.py                      # update router imports only
  worker.py                    # update task imports only
  app/
    platform/                  # UNCHANGED except env.py import list
      config.py
      context.py
      db.py
      http.py                  # update exception imports → app.exceptions.*
      logging.py
      repository.py
      storage.py
      queue.py
      seams/
        __init__.py
        protocols.py
        types.py
        fakes.py
        real_parser.py
        real_llm.py
        factory.py
      # NEW: move auth helpers here
      tokens.py                # moved from identity/tokens.py
      passwords.py             # moved from identity/passwords.py
      constants.py             # moved from identity/constants.py

    models/                    # M — all SQLAlchemy ORM models
      __init__.py              # re-export all model classes + Base
      identity.py              # Organization, User    (from identity/models.py)
      documents.py             # Folder, Tag, Document, DocumentTag, DocumentStatus
                               #   (from documents/models.py + documents/status.py)
      ingestion.py             # Section, Chunk, Embedding  (from ingestion/models.py)
      knowledge.py             # Notebook, NotebookDocument (from knowledge/models.py)
      chat.py                  # Conversation, Message  (from chat/models.py)

    schemas/                   # V — all Pydantic request/response shapes (wire layer)
      __init__.py
      auth.py                  # SignupRequest, LoginRequest, InviteRequest,
                               #   RoleChangeRequest, TokenResponse, UserOut,
                               #   OrgChoice, LoginAmbiguousResponse
                               #   (from identity/schemas.py)
      documents.py             # FolderCreate, FolderOut, FolderRename, FolderMove,
                               #   TagCreate, TagOut, DocumentOut
                               #   (from documents/schemas.py)
      ingestion.py             # ChunkHit, ChunkRecord   (from ingestion/schemas.py)
      knowledge.py             # NotebookCreate, NotebookUpdate, NotebookOut
                               #   (from knowledge/schemas.py)
      retrieval.py             # RetrievalSearchRequest, ContextBlock,
                               #   RetrievalSearchResponse (from retrieval/schemas.py)
      chat.py                  # ChatRequest, ResolvedCitation, ChatResponse
                               #   (from chat/schemas.py)

    controllers/               # C — all FastAPI routers (thin HTTP handlers)
      __init__.py
      auth.py                  # prefix=/auth  (from identity/router.py)
      documents.py             # prefix=/documents  (from documents/router.py)
      ingestion.py             # prefix=/ingestion  (from ingestion/router.py)
      notebooks.py             # prefix=/notebooks  (from knowledge/router.py)
      retrieval.py             # prefix=/retrieval  (from retrieval/router.py)
      chat.py                  # prefix=/chat  (from chat/router.py)
      deps.py                  # current_user, get_ctx, require_admin
                               #   (from identity/deps.py)

    services/                  # Business logic (one unit per domain)
      __init__.py
      auth.py                  # AuthService + auth_service singleton
                               #   (from identity/service.py)
      documents/               # KEEP as subpackage (was already >200 lines)
        __init__.py            # DocumentsService + documents_service singleton
        folders.py
        tags.py
        documents.py
      ingestion/               # KEEP as subpackage (was already >200 lines)
        __init__.py            # IngestionService + ingestion_service singleton
        parsing.py
        structuring.py
        embedding.py
        search.py
      knowledge.py             # KnowledgeService + knowledge_service singleton
      retrieval.py             # RetrievalService, resolve_allowed_documents,
                               #   assemble_context
      chat.py                  # ChatService, build_messages, generate_answer,
                               #   call_llm_with_retry, parse_citation_markers,
                               #   resolve_citations

    repositories/              # Data access (one unit per domain)
      __init__.py
      auth.py                  # OrganizationRepository, AuthRepository, UserRepository
                               #   (from identity/repository.py)
      documents/               # KEEP as subpackage (was already a subpackage)
        __init__.py
        folders.py             # FolderRepository
        tags.py                # TagRepository, DocumentTagRepository
        documents.py           # DocumentRepository
      ingestion.py             # SectionRepository, ChunkRepository, EmbeddingRepository
                               #   (from ingestion/repository.py)
      knowledge.py             # NotebookRepository, NotebookDocumentRepository
                               #   (from knowledge/repository.py)
      chat.py                  # ConversationRepository, MessageRepository
                               #   (from chat/repository.py)

    exceptions/                # All domain exceptions
      __init__.py              # re-export all
      auth.py                  # AuthError hierarchy  (from identity/exceptions.py)
      documents.py             # DocumentsError hierarchy (from documents/exceptions.py)
      knowledge.py             # KnowledgeError, NotebookNotFound
      chat.py                  # GenerationFailed

    tasks/                     # arq background job functions
      __init__.py
      ingestion.py             # run_parsing_stage_job, run_structuring_stage_job,
                               #   run_embedding_stage_job (from ingestion/tasks.py)
```

### 2b. Frontend — target

```
frontend/src/
  main.tsx                     # unchanged
  App.tsx                      # update feature imports → views/
  index.css
  vite-env.d.ts

  models/                      # M — TypeScript type definitions
    index.ts                   # re-export everything
    auth.ts                    # User, TokenResponse, OrgChoice, LoginAmbiguousResponse
    documents.ts               # Folder, Tag, Document, DocumentStatus,
                               #   FolderDeleteMode
    knowledge.ts               # Notebook
    chat.ts                    # ResolvedCitation, ChatResponse, ChatRequest,
                               #   SSETokenEvent, SSEDoneEvent, SSEErrorEvent, SSEEvent

  controllers/                 # C — API call functions + custom hooks
    index.ts                   # re-export all api namespaces
    authController.ts          # authApi namespace  (extracted from lib/api.ts)
    documentsController.ts     # documentsApi namespace
    notebooksController.ts     # notebooksApi namespace
    chatController.ts          # chatApi namespace (streamAsk + cleanup)
    # Keep in lib/:
    # lib/api.ts → thin HTTP util only: apiFetch, getStoredAccessToken,
    #              setStoredAccessToken — NO type definitions, NO api namespaces

  views/                       # V — React page/feature components
    app/
      AppShell.tsx             # (from features/app/)
      Sidebar.tsx
      HomePage.tsx
    auth/
      LoginPage.tsx            # (from features/auth/)
      SignupPage.tsx
    documents/
      DocumentsPage.tsx        # (from features/documents/)
      FolderTree.tsx
      FolderTree.test.tsx
      DocumentList.tsx
      DocumentList.test.tsx
    notebooks/
      NotebookList.tsx         # (from features/notebooks/)
      NotebookList.test.tsx
      NotebookPage.tsx
      NotebookPage.test.tsx
    chat/
      ChatPanel.tsx            # (from features/chat/)
      ChatPanel.test.tsx
      CitationPanel.tsx
    users/
      UsersPage.tsx

  components/                  # shared UI — UNCHANGED
    ProtectedRoute.tsx
    StatusBadge.tsx

  lib/
    api.ts                     # TRIMMED: only apiFetch util + token helpers
                               #   (types move to models/, namespaces move to controllers/)
    auth.tsx                   # UNCHANGED: AuthProvider, useAuth

  test/
    setup.ts                   # unchanged
```

---

## 3. Precise file-by-file move map

### Backend

| Old path | New path | Notes |
|---|---|---|
| `app/identity/models.py` | `app/models/identity.py` | rename classes if needed |
| `app/documents/models.py` | `app/models/documents.py` | merge with status.py |
| `app/documents/status.py` | merged into `app/models/documents.py` | DocumentStatus StrEnum |
| `app/ingestion/models.py` | `app/models/ingestion.py` | |
| `app/knowledge/models.py` | `app/models/knowledge.py` | |
| `app/chat/models.py` | `app/models/chat.py` | |
| `app/identity/schemas.py` | `app/schemas/auth.py` | |
| `app/documents/schemas.py` | `app/schemas/documents.py` | |
| `app/ingestion/schemas.py` | `app/schemas/ingestion.py` | |
| `app/knowledge/schemas.py` | `app/schemas/knowledge.py` | |
| `app/retrieval/schemas.py` | `app/schemas/retrieval.py` | |
| `app/chat/schemas.py` | `app/schemas/chat.py` | |
| `app/identity/router.py` | `app/controllers/auth.py` | |
| `app/documents/router.py` | `app/controllers/documents.py` | |
| `app/ingestion/router.py` | `app/controllers/ingestion.py` | |
| `app/knowledge/router.py` | `app/controllers/notebooks.py` | |
| `app/retrieval/router.py` | `app/controllers/retrieval.py` | |
| `app/chat/router.py` | `app/controllers/chat.py` | |
| `app/identity/deps.py` | `app/controllers/deps.py` | |
| `app/identity/service.py` | `app/services/auth.py` | |
| `app/documents/service/` | `app/services/documents/` | keep subpackage |
| `app/ingestion/service/` | `app/services/ingestion/` | keep subpackage |
| `app/knowledge/service.py` | `app/services/knowledge.py` | |
| `app/retrieval/service.py` | `app/services/retrieval.py` | |
| `app/chat/service.py` | `app/services/chat.py` | |
| `app/identity/repository.py` | `app/repositories/auth.py` | |
| `app/documents/repository/` | `app/repositories/documents/` | keep subpackage |
| `app/ingestion/repository.py` | `app/repositories/ingestion.py` | |
| `app/knowledge/repository.py` | `app/repositories/knowledge.py` | |
| `app/chat/repository.py` | `app/repositories/chat.py` | |
| `app/identity/exceptions.py` | `app/exceptions/auth.py` | |
| `app/documents/exceptions.py` | `app/exceptions/documents.py` | |
| `app/knowledge/exceptions.py` | `app/exceptions/knowledge.py` | |
| `app/chat/exceptions.py` | `app/exceptions/chat.py` | |
| `app/identity/tokens.py` | `app/platform/tokens.py` | |
| `app/identity/passwords.py` | `app/platform/passwords.py` | |
| `app/identity/constants.py` | `app/platform/constants.py` | |
| `app/ingestion/tasks.py` | `app/tasks/ingestion.py` | |

### Frontend

| Old path | New path |
|---|---|
| `src/features/app/AppShell.tsx` | `src/views/app/AppShell.tsx` |
| `src/features/app/Sidebar.tsx` | `src/views/app/Sidebar.tsx` |
| `src/features/app/HomePage.tsx` | `src/views/app/HomePage.tsx` |
| `src/features/auth/LoginPage.tsx` | `src/views/auth/LoginPage.tsx` |
| `src/features/auth/SignupPage.tsx` | `src/views/auth/SignupPage.tsx` |
| `src/features/documents/DocumentsPage.tsx` | `src/views/documents/DocumentsPage.tsx` |
| `src/features/documents/FolderTree.tsx` | `src/views/documents/FolderTree.tsx` |
| `src/features/documents/FolderTree.test.tsx` | `src/views/documents/FolderTree.test.tsx` |
| `src/features/documents/DocumentList.tsx` | `src/views/documents/DocumentList.tsx` |
| `src/features/documents/DocumentList.test.tsx` | `src/views/documents/DocumentList.test.tsx` |
| `src/features/notebooks/NotebookList.tsx` | `src/views/notebooks/NotebookList.tsx` |
| `src/features/notebooks/NotebookList.test.tsx` | `src/views/notebooks/NotebookList.test.tsx` |
| `src/features/notebooks/NotebookPage.tsx` | `src/views/notebooks/NotebookPage.tsx` |
| `src/features/notebooks/NotebookPage.test.tsx` | `src/views/notebooks/NotebookPage.test.tsx` |
| `src/features/chat/ChatPanel.tsx` | `src/views/chat/ChatPanel.tsx` |
| `src/features/chat/ChatPanel.test.tsx` | `src/views/chat/ChatPanel.test.tsx` |
| `src/features/chat/CitationPanel.tsx` | `src/views/chat/CitationPanel.tsx` |
| `src/features/users/UsersPage.tsx` | `src/views/users/UsersPage.tsx` |
| Types in `src/lib/api.ts` | `src/models/` (split by domain, see §3b) |
| API namespaces in `src/lib/api.ts` | `src/controllers/` (split by domain, see §3b) |

### Frontend type split (from lib/api.ts into models/)

Create `src/models/auth.ts`:
- `User`, `TokenResponse`, `ApiError`

Create `src/models/documents.ts`:
- `Folder`, `Tag`, `DocumentStatus`, `Document`, `FolderDeleteMode`

Create `src/models/knowledge.ts`:
- `Notebook`

Create `src/models/chat.ts`:
- `ResolvedCitation`, `ChatResponse`, `ChatRequest`,
  `SSETokenEvent`, `SSEDoneEvent`, `SSEErrorEvent`, `SSEEvent`

Create `src/models/index.ts` — re-export everything from the above.

### Frontend controller split (from lib/api.ts into controllers/)

Create `src/controllers/authController.ts`:
- `authApi` namespace (signup, login, refresh, logout, me, listUsers, changeRole)

Create `src/controllers/documentsController.ts`:
- `documentsApi` namespace (listFolders, createFolder, renameFolder, moveFolder,
  deleteFolder, listTags, createTag, deleteTag, tagDocument, untagDocument,
  listDocuments, uploadDocument)

Create `src/controllers/notebooksController.ts`:
- `notebooksApi` namespace (list, get, create, update, delete, listDocuments,
  attachDocument, detachDocument)

Create `src/controllers/chatController.ts`:
- `chatApi` namespace (streamAsk — fetch + ReadableStream + AbortController cleanup)

Create `src/controllers/index.ts` — re-export all four namespaces.

Trim `src/lib/api.ts` to contain ONLY:
- `getStoredAccessToken()`, `setStoredAccessToken()`
- `apiFetch<T>()` — the shared HTTP fetch helper

---

## 4. All import paths that must change

### Backend — systematic search-and-replace list

After moving files, every `from app.X import Y` must be updated. Here is the complete
mapping. Update both source files AND all test files.

| Old import prefix | New import prefix |
|---|---|
| `from app.identity.models import` | `from app.models.identity import` |
| `from app.documents.models import` | `from app.models.documents import` |
| `from app.documents.status import` | `from app.models.documents import` |
| `from app.ingestion.models import` | `from app.models.ingestion import` |
| `from app.knowledge.models import` | `from app.models.knowledge import` |
| `from app.chat.models import` | `from app.models.chat import` |
| `from app.identity.schemas import` | `from app.schemas.auth import` |
| `from app.documents.schemas import` | `from app.schemas.documents import` |
| `from app.ingestion.schemas import` | `from app.schemas.ingestion import` |
| `from app.knowledge.schemas import` | `from app.schemas.knowledge import` |
| `from app.retrieval.schemas import` | `from app.schemas.retrieval import` |
| `from app.chat.schemas import` | `from app.schemas.chat import` |
| `from app.identity.exceptions import` | `from app.exceptions.auth import` |
| `from app.documents.exceptions import` | `from app.exceptions.documents import` |
| `from app.knowledge.exceptions import` | `from app.exceptions.knowledge import` |
| `from app.chat.exceptions import` | `from app.exceptions.chat import` |
| `from app.identity.service import` | `from app.services.auth import` |
| `from app.documents.service import` | `from app.services.documents import` |
| `from app.ingestion.service import` | `from app.services.ingestion import` |
| `from app.knowledge.service import` | `from app.services.knowledge import` |
| `from app.retrieval.service import` | `from app.services.retrieval import` |
| `from app.chat.service import` | `from app.services.chat import` |
| `from app.identity.repository import` | `from app.repositories.auth import` |
| `from app.documents.repository import` | `from app.repositories.documents import` |
| `from app.ingestion.repository import` | `from app.repositories.ingestion import` |
| `from app.knowledge.repository import` | `from app.repositories.knowledge import` |
| `from app.chat.repository import` | `from app.repositories.chat import` |
| `from app.identity.deps import` | `from app.controllers.deps import` |
| `from app.identity.tokens import` | `from app.platform.tokens import` |
| `from app.identity.passwords import` | `from app.platform.passwords import` |
| `from app.identity.constants import` | `from app.platform.constants import` |
| `from app.ingestion.tasks import` | `from app.tasks.ingestion import` |
| `from app.identity.router import` | `from app.controllers.auth import` |
| `from app.documents.router import` | `from app.controllers.documents import` |
| `from app.ingestion.router import` | `from app.controllers.ingestion import` |
| `from app.knowledge.router import` | `from app.controllers.notebooks import` |
| `from app.retrieval.router import` | `from app.controllers.retrieval import` |
| `from app.chat.router import` | `from app.controllers.chat import` |

**Special case — `migrations/env.py`:** This file imports all ORM model classes so
Alembic can see them. After the refactor it must import from the new paths:
```python
# Old:
from app.identity.models import Organization, User
from app.documents.models import Folder, Tag, Document, DocumentTag
from app.ingestion.models import Section, Chunk, Embedding
from app.knowledge.models import Notebook, NotebookDocument
from app.chat.models import Conversation, Message
# New:
from app.models.identity import Organization, User
from app.models.documents import Folder, Tag, Document, DocumentTag
from app.models.ingestion import Section, Chunk, Embedding
from app.models.knowledge import Notebook, NotebookDocument
from app.models.chat import Conversation, Message
```

**Special case — `app/platform/http.py`:** `register_exception_handlers()` imports all
exception classes. After the refactor, update to import from `app.exceptions.*`.

**Special case — `app/platform/repository.py`:** `BaseRepository` imports `Base` from
`app.platform.db`. It does NOT import from any domain model — this stays unchanged.

**Special case — `scripts/inspect_document.py`:** This debug script imports
`_build_sections_and_chunks` directly from `app.ingestion.service.structuring`. After
the refactor, update to `from app.services.ingestion.structuring import _build_sections_and_chunks`.

### Frontend

After moving files, update every import in every `.tsx`/`.ts` file. The key changes:

- In all `views/**/*.tsx`: change `from '../../lib/api'` type imports
  → `from '../../models'` and api namespace imports → `from '../../controllers'`
- In `App.tsx`: change `from './features/...'` → `from './views/...'`
- In `components/ProtectedRoute.tsx` and `components/StatusBadge.tsx`: update
  `lib/api` type imports → `models/`

Relative import depths will change because files have moved. Adjust `../` counts
accordingly (e.g., a file in `views/documents/` needs `../../` to reach `models/`).

---

## 5. Hard constraints — must not be violated

These are architectural rules baked into the codebase. Do not break them during
the refactor.

### 5a. Module boundary rule (unchanged)
A service may call another module ONLY through its service singleton — never its
repository or ORM models directly. This rule is enforced by the fact that services
import other services (e.g., `chat.service` imports `retrieval_service`). After the
refactor this becomes:
- `services/chat.py` imports `from app.services.retrieval import retrieval_service`
- `services/retrieval.py` imports `from app.services.knowledge import knowledge_service`
  and `from app.services.ingestion import ingestion_service`
- `services/knowledge.py` imports `from app.services.documents import documents_service`

### 5b. Circular-import composition point (critical)
`services/ingestion/__init__.py` already imports `from app.services.documents import
documents_service`. Therefore `services/documents` cannot import from
`services/ingestion`. The pipeline composition (upload → enqueue) is done in
`controllers/documents.py` (the router), NOT in `services/documents`. Do not move
this composition into a service — the circular import would break Python's import system.

### 5c. No SQL outside repositories
No raw SQL or ORM queries may appear in services, controllers, or anywhere except
`repositories/**/*.py`. Do not reorganize this during the refactor.

### 5d. No business logic in controllers
Controllers (routers) only: parse HTTP input, call one service method, return HTTP
response. Do not move business logic into controllers.

### 5e. Every DB query stays scoped by org_id
`BaseRepository._scoped()` is the choke-point. It is in `app/platform/repository.py`
which is NOT moving. No change needed here, but do not accidentally bypass it.

### 5f. arq worker isolation
`worker.py` must import task functions from the new `app/tasks/ingestion.py`. The
worker uses arq's redis pool; it must import `ArqJobQueue` from `app.platform.queue`.

### 5g. Alembic metadata registration
After moving ORM models, `migrations/env.py` must import every model class from the
new `app.models.*` paths before `target_metadata = Base.metadata` is read, or
Alembic will silently miss tables and generate wrong migrations.

---

## 6. What must NOT change (do not touch)

- **`migrations/versions/*.py`** — migration files are immutable history. Only update
  `migrations/env.py` (the import lines at the top, not the migration bodies).
- **`tests/`** — test logic, assertions, and fixtures stay identical. Only update
  import paths inside test files.
- **`docker-compose.yml`** — infra unchanged.
- **`pyproject.toml`** — no dependency changes.
- **`frontend/package.json`** — no dependency changes.
- **`vite.config.ts`** — proxy config unchanged (/auth, /documents, /notebooks, /chat,
  /health all still work because endpoint paths in controllers don't change). Note this
  list is deliberately not all 6 mounted routers — `/ingestion` and `/retrieval` have
  never been proxied because the SPA never calls them directly; that's a pre-existing
  fact unrelated to this refactor, not something this refactor needs to add.
- **`frontend/src/test/setup.ts`** — test setup unchanged.
- **All HTTP endpoint paths** — `/auth/*`, `/documents/*`, `/ingestion/*`,
  `/notebooks/*`, `/retrieval/*`, `/chat/*` — must remain identical.
- **All Pydantic schema field names** — the wire API contract is unchanged.
- **All ORM column names and table names** — no DB changes.
- **`app/platform/seams/`** — unchanged.
- **`app/platform/storage.py`**, **`app/platform/queue.py`**, **`app/platform/db.py`**,
  **`app/platform/config.py`**, **`app/platform/context.py`**,
  **`app/platform/logging.py`**, **`app/platform/repository.py`** — unchanged.

---

## 7. Recommended execution order

Do the backend first (it is the larger change), then the frontend. Within each,
move bottom-up: lowest-dependency files first, highest-dependency files last.

### Backend order

1. **Create directory skeleton** — mkdir all new dirs:
   `app/models/`, `app/schemas/`, `app/controllers/`, `app/services/`,
   `app/repositories/`, `app/exceptions/`, `app/tasks/`

2. **Move platform auth helpers** (no deps on domain code):
   `identity/tokens.py` → `platform/tokens.py`
   `identity/passwords.py` → `platform/passwords.py`
   `identity/constants.py` → `platform/constants.py`

3. **Move ORM models** (only depend on `platform.db.Base`):
   - `identity/models.py` → `models/identity.py`
   - `documents/models.py` + `documents/status.py` → `models/documents.py`
   - `ingestion/models.py` → `models/ingestion.py`
   - `knowledge/models.py` → `models/knowledge.py`
   - `chat/models.py` → `models/chat.py`
   - Write `models/__init__.py` re-exporting all classes

4. **Move exceptions** (no deps on domain code):
   - `identity/exceptions.py` → `exceptions/auth.py`
   - `documents/exceptions.py` → `exceptions/documents.py`
   - `knowledge/exceptions.py` → `exceptions/knowledge.py`
   - `chat/exceptions.py` → `exceptions/chat.py`
   - Write `exceptions/__init__.py`

5. **Update `platform/http.py`** — update exception imports → `app.exceptions.*`

6. **Move schemas** (depend on models and possibly exceptions):
   - `identity/schemas.py` → `schemas/auth.py`
   - `documents/schemas.py` → `schemas/documents.py`
   - `ingestion/schemas.py` → `schemas/ingestion.py`
   - `knowledge/schemas.py` → `schemas/knowledge.py`
   - `retrieval/schemas.py` → `schemas/retrieval.py`
   - `chat/schemas.py` → `schemas/chat.py`
   - Write `schemas/__init__.py`

7. **Move repositories** (depend on models, platform.repository, platform.context):
   - `identity/repository.py` → `repositories/auth.py`
   - `documents/repository/` → `repositories/documents/` (copy whole subpackage)
   - `ingestion/repository.py` → `repositories/ingestion.py`
   - `knowledge/repository.py` → `repositories/knowledge.py`
   - `chat/repository.py` → `repositories/chat.py`
   - Write `repositories/__init__.py`

8. **Move services** (depend on repositories, schemas, models, seams, platform):
   - `identity/service.py` → `services/auth.py`
   - `documents/service/` → `services/documents/` (copy whole subpackage)
   - `ingestion/service/` → `services/ingestion/` (copy whole subpackage)
   - `knowledge/service.py` → `services/knowledge.py`
   - `retrieval/service.py` → `services/retrieval.py`
   - `chat/service.py` → `services/chat.py`
   - Write `services/__init__.py`

9. **Move tasks**:
   - `ingestion/tasks.py` → `tasks/ingestion.py`
   - Write `tasks/__init__.py`

10. **Move controllers + deps** (depend on services, schemas, platform deps):
    - `identity/deps.py` → `controllers/deps.py`
    - `identity/router.py` → `controllers/auth.py`
    - `documents/router.py` → `controllers/documents.py`
    - `ingestion/router.py` → `controllers/ingestion.py`
    - `knowledge/router.py` → `controllers/notebooks.py`
    - `retrieval/router.py` → `controllers/retrieval.py`
    - `chat/router.py` → `controllers/chat.py`
    - Write `controllers/__init__.py`

11. **Update `migrations/env.py`** — change all model import paths.

12. **Update `main.py`** — change router imports to `app.controllers.*`.

13. **Update `worker.py`** — change task imports to `app.tasks.ingestion`.

14. **Update all test files** — grep for old import paths, update to new paths.

15. **Delete old domain directories** — only after all tests pass:
    `app/identity/`, `app/documents/`, `app/ingestion/`, `app/knowledge/`,
    `app/retrieval/`, `app/chat/`

16. **Run tests**: `cd backend && pytest` — all 105+ tests must pass.
    Also run `ruff check . && ruff format --check .`.

### Frontend order

1. **Create directory skeleton**: `src/models/`, `src/controllers/`, `src/views/`

2. **Create `src/models/` files** — extract type definitions from `lib/api.ts`:
   `auth.ts`, `documents.ts`, `knowledge.ts`, `chat.ts`, `index.ts`

3. **Create `src/controllers/` files** — extract API namespaces from `lib/api.ts`:
   `authController.ts`, `documentsController.ts`, `notebooksController.ts`,
   `chatController.ts`, `index.ts`

4. **Trim `src/lib/api.ts`** — remove type definitions and API namespaces; keep only
   `getStoredAccessToken`, `setStoredAccessToken`, `apiFetch`.

5. **Move view files** — copy/move all `features/**` to `views/**`.

6. **Update imports in all moved view files** — point type imports to `models/` and
   API namespace imports to `controllers/`. Adjust relative `../` depth as needed.

7. **Update `App.tsx`** — change feature component imports to `views/`.

8. **Update `lib/auth.tsx`** — if it imports from `lib/api.ts`, update to use `models/`
   for types.

9. **Delete `src/features/`** — after tests pass.

10. **Run frontend tests**: `cd frontend && npx vitest run` — all 30 tests must pass.
    Also run `npx tsc -b && npx vite build`.

---

## 8. Verification checklist

After completing the refactor, verify every item:

### Backend
- [x] `pytest` passes — **re-run with Docker available (2026-07-01): 135 passed, 1
      skipped, 0 failures.** The 1 skip is the opt-in `real_parser` integration test
      (requires a live `OPENROUTER_API_KEY`, deliberately excluded from the default
      run — unrelated to Docker). This closes out the earlier gap: an initial pass in a
      sandbox where Docker Desktop would not launch had only 43 passed / 93 skipped
      (all `Docker not available`); confirmed here, with real Testcontainers Postgres,
      that the refactor is a true zero-logic-change — same 43 fake/offline tests plus
      all 92 previously-skipped DB-backed tests now pass unchanged.
- [x] `ruff check .` — 0 new errors. 3 remaining findings (2 `E402` + 1 `B905`, all in
      `scripts/inspect_document.py`) are pre-existing per `memory.md`, untouched by this
      refactor.
- [x] `ruff format --check .` — 0 diffs (108 files already formatted)
- [x] `python -c "from app.models import Organization, User, Folder, Document,
      Section, Chunk, Embedding, Notebook, Conversation, Message"` — no import error
- [x] `python -c "from app.controllers.auth import router"` — no import error
- [x] Alembic metadata check — `app.models` registers all 13 tables on `Base.metadata`
      (`chunks, conversations, document_tags, documents, embeddings, folders,
      knowledge_base_documents, knowledge_bases, messages, organizations, sections,
      tags, users`); `migrations/env.py` imports updated to `app.models.*`. Full
      `alembic upgrade --sql head` against a live DB not run here (no Docker — see above).
- [x] `GET /health` returns 200 (verified via `TestClient`)
- [x] Old directories `app/identity/`, `app/documents/`, `app/ingestion/`,
      `app/knowledge/`, `app/retrieval/`, `app/chat/` are fully deleted

### Frontend
- [x] `npx vitest run` — 30/30 tests pass
- [x] `npx tsc -b` — 0 type errors
- [x] `npx vite build` — 0 build errors
- [x] `src/features/` is fully deleted
- [x] No `import from '*/lib/api'` in any file except `lib/api.ts` itself,
      `lib/auth.tsx`, and the controllers (for `apiFetch`)

---

## 9. Key gotchas to watch for

1. **`models/__init__.py` must import all ORM model classes** so that `Base.metadata`
   is populated when `migrations/env.py` does `from app.models import Base`.
   **Correction (verified 2026-07-01): import ORDER does not actually matter.** Every
   FK in this codebase is a string table-name reference (`ForeignKey("organizations.id")`),
   never a direct class reference or `relationship()`, so there is no load-time
   dependency between model modules — `ruff`'s isort will alphabetize these imports on
   every `ruff check --fix`/format pass, and that's fine, don't fight it with `noqa`
   pragmas or manual reordering. (An earlier draft of this gotcha claimed a required
   "identity → documents → ingestion → knowledge → chat" order; that claim was wrong
   and has been removed from the code comments in `app/models/__init__.py` and
   `migrations/env.py` too.) The only real requirement is that every model module gets
   imported *somewhere* before `Base.metadata` is read — which order is irrelevant.

2. **`services/documents/__init__.py` and `services/ingestion/__init__.py`** both use
   a free-function delegation pattern (the service class calls module-level functions
   defined in sibling files like `folders.py`, `parsing.py`). Preserve this exact
   pattern — do not convert to class methods.

3. **`onupdate=func.now()` is NOT used in this codebase.** `updated_at` is set
   explicitly in repository `update` methods. Do not add `onupdate` when copying ORM
   models.

4. **`DocumentTag` is a pure join table** with no Python-side relationship helpers —
   `DocumentTagRepository.attach` uses `INSERT ... ON CONFLICT DO NOTHING`. Preserve
   this; do not add SQLAlchemy `relationship()` calls.

5. **`Embedding.id` is a regular UUID, not deterministic.** `Chunk.id` IS
   deterministic (sha256 of document_id|ordinal|content). Do not swap these.

6. **`BaseRepository._scoped()`** is in `platform/repository.py` and is NOT moving.
   Every repository `__init__` calls `super().__init__(session, ctx)`. Make sure all
   moved repositories still inherit from `BaseRepository` imported from
   `app.platform.repository`.

7. **arq job context dict**: `run_parsing_stage_job(ctx: dict, *, org_id: str,
   document_id: str)` — the first arg is arq's internal context, not TenantContext.
   The job functions build a TenantContext internally. Do not change this signature.

8. **`FolderCycleError`** from `exceptions/documents.py` is NOT yet wired into
   `platform/http.py`. Do not add it — leave the existing gap as-is.

9. **Frontend: `accumulated` local var pattern in `ChatPanel.tsx`** — tokens are
   captured in a closure-local string, not React state. Do not convert to `useState`
   when moving the file.

10. **Frontend: `citations === undefined` sentinel** — `undefined` means still
    streaming; defined (even `[]`) means final. This is in `ChatPanel.tsx`. Preserve
    exactly.

11. **Frontend relative imports**: `features/<area>/File.tsx` and `views/<area>/File.tsx`
    are the same depth from `src/` (both two levels deep), so a plain move of `features/`
    → `views/` does NOT change any `../` counts to `lib/` or `components/` — no path-depth
    arithmetic needed for that part. What DOES change is *what* those imports point at:
    after splitting `lib/api.ts`, an import that previously did
    `import { Folder } from '../../lib/api'` now needs
    `import { Folder } from '../../models'` (types) and/or
    `import { documentsApi } from '../../controllers'` (API namespaces) — same `../../`
    depth, different target module. Audit every moved file for this target swap.

12. **`test_layout_smoke.py` imports `worker.WorkerSettings`** — after updating
    `worker.py`'s task imports, make sure `WorkerSettings` is still exported at module
    level.
