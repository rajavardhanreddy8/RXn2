import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import HostedApp from './HostedApp'
import './styles.css'

const RootApp = import.meta.env.VITE_RXN2_HOSTED_API ? HostedApp : App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootApp />
  </StrictMode>,
)
