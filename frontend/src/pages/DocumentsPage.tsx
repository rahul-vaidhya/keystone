import { useState } from "react";
import { FolderTree } from "../components/FolderTree";
import { DocumentList } from "../components/DocumentList";

export function DocumentsPage() {
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);

  return (
    <div className="flex flex-1 min-h-0">
      <aside className="w-56 shrink-0 border-r border-border overflow-y-auto py-3">
        <FolderTree currentFolderId={currentFolderId} onNavigate={setCurrentFolderId} />
      </aside>
      <DocumentList currentFolderId={currentFolderId} />
    </div>
  );
}
