import { useState } from "react";
import { FolderTree } from "../components/FolderTree";
import { DocumentList } from "../components/DocumentList";

export function DocumentsPage() {
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);

  return (
    <div className="flex flex-col lg:flex-row flex-1 min-h-0">
      <aside className="w-full max-h-48 border-b lg:max-h-none lg:w-56 lg:border-b-0 lg:border-r shrink-0 border-border overflow-y-auto py-3">
        <FolderTree currentFolderId={currentFolderId} onNavigate={setCurrentFolderId} />
      </aside>
      <DocumentList currentFolderId={currentFolderId} />
    </div>
  );
}
