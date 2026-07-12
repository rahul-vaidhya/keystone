import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AdminRoute, ProtectedRoute } from "./components/ProtectedRoute";
import { AppShell } from "./layouts/AppShell";
import { HomePage } from "./pages/HomePage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { NotebookList } from "./pages/NotebookList";
import { NotebookPage } from "./pages/NotebookPage";
import { LoginPage } from "./pages/LoginPage";
import { SignupPage } from "./pages/SignupPage";
import { UsersPage } from "./pages/UsersPage";
import { AccessRolesPage } from "./pages/AccessRolesPage";
import { AuthProvider } from "./context/AuthContext";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Navigate to="/app" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/app" element={<AppShell />}>
                <Route index element={<HomePage />} />
                <Route path="repository" element={<DocumentsPage />} />
                <Route path="notebooks" element={<NotebookList />} />
                <Route path="notebooks/:notebookId" element={<NotebookPage />} />
                <Route element={<AdminRoute />}>
                  <Route path="users" element={<UsersPage />} />
                  <Route path="access-roles" element={<AccessRolesPage />} />
                </Route>
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/app" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
