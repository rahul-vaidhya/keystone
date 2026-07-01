import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AdminRoute, ProtectedRoute } from "./components/ProtectedRoute";
import { AppShell } from "./views/app/AppShell";
import { HomePage } from "./views/app/HomePage";
import { DocumentsPage } from "./views/documents/DocumentsPage";
import { NotebookList } from "./views/notebooks/NotebookList";
import { NotebookPage } from "./views/notebooks/NotebookPage";
import { LoginPage } from "./views/auth/LoginPage";
import { SignupPage } from "./views/auth/SignupPage";
import { UsersPage } from "./views/users/UsersPage";
import { AuthProvider } from "./lib/auth";

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
