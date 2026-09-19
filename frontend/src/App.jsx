import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import AdminLayout from './pages/admin/AdminLayout'
import Compare from './pages/admin/Compare'
import Labelling from './pages/admin/Labelling'
import Review from './pages/admin/Review'
import Vocabulary from './pages/admin/Vocabulary'
import AuthPage from './pages/AuthPage'
import Bookmarks from './pages/Bookmarks'
import Frequent from './pages/Frequent'
import Home from './pages/Home'
import QuestionList from './pages/QuestionList'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/speaking/task2" element={<QuestionList tache={2} />} />
        <Route path="/speaking/task3" element={<QuestionList tache={3} />} />
        <Route path="/tools/bookmarks" element={<Bookmarks />} />
        <Route path="/core-set" element={<Frequent />} />
        {/* the page lived under /tools before it was promoted */}
        <Route path="/tools/frequent" element={<Navigate to="/core-set" replace />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/vocabulary" replace />} />
          <Route path="vocabulary" element={<Vocabulary />} />
          <Route path="questions" element={<Labelling />} />
          <Route path="reviews" element={<Review />} />
          <Route path="compare" element={<Compare />} />
        </Route>
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/signup" element={<AuthPage mode="signup" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
