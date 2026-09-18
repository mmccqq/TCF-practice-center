import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
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
        <Route path="/tools/frequent" element={<Frequent />} />
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/signup" element={<AuthPage mode="signup" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
