import { MemberGate } from './components/MemberGate'
import { HomePage } from './pages/HomePage'

export default function App() {
  return (
    <MemberGate>
      <HomePage />
    </MemberGate>
  )
}