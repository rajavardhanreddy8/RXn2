import { useState, useEffect, FormEvent } from 'react'
import {
  fetchCurrentUser,
  loginUser,
  registerUser,
  logoutUser,
  fetchProjects,
  createProject,
  updateProject,
  deleteProject,
  fetchProjectRoutes,
  createProjectRoute,
  updateProjectRoute,
  deleteProjectRoute,
} from './api'
import RouteComparison from './RouteComparison'
import type { User, Project, SavedRoute, SavedStep, MassUnit, DurationUnit } from './types'

const benchmarkSuggestions = [
  { name: 'Demo benzamide target', id: 'DEMO-TARGET-1' },
  { name: 'Apixaban', id: 'BENCH-APIXABAN' },
  { name: 'Ibuprofen', id: 'BENCH-IBUPROFEN' },
  { name: 'Acetaminophen', id: 'BENCH-ACETAMINOPHEN' },
  { name: 'Metformin', id: 'BENCH-METFORMIN' },
  { name: 'Sildenafil', id: 'BENCH-SILDENAFIL' },
]

export default function ProjectWorkspace() {
  // Auth state
  const [user, setUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [authError, setAuthError] = useState('')

  // Workspace / Projects state
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<Project | null>(null)
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [projectsError, setProjectsError] = useState('')
  const [projectModalOpen, setProjectModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)

  // Project Form state
  const [projectTitle, setProjectTitle] = useState('')
  const [projectTargetName, setProjectTargetName] = useState('')
  const [projectTargetId, setProjectTargetId] = useState('')
  const [projectMass, setProjectMass] = useState(1000)
  const [projectMassUnit, setProjectMassUnit] = useState<MassUnit>('g')
  const [projectNotes, setProjectNotes] = useState('')
  const [projectFormError, setProjectFormError] = useState('')

  // Routes state
  const [routes, setRoutes] = useState<SavedRoute[]>([])
  const [routesLoading, setRoutesLoading] = useState(false)
  const [routesError, setRoutesError] = useState('')
  const [editingRoute, setEditingRoute] = useState<SavedRoute | null>(null)
  const [routeEditorOpen, setRouteEditorOpen] = useState(false)
  const [editorSteps, setEditorSteps] = useState<SavedStep[]>([])
  const [editorOrigin, setEditorOrigin] = useState<'manual' | 'generated'>('manual')
  const [editorError, setEditorError] = useState('')
  const [saveSuccessMsg, setSaveSuccessMsg] = useState('')
  const [workspaceTab, setWorkspaceTab] = useState<'routes' | 'compare'>('routes')

  // Load current user on mount
  useEffect(() => {
    checkAuth()
  }, [])

  // Load projects whenever user logs in
  useEffect(() => {
    if (user) {
      loadProjects()
    } else {
      setProjects([])
      setSelectedProject(null)
      setRoutes([])
    }
  }, [user])

  // Load routes when project is selected
  useEffect(() => {
    if (selectedProject) {
      loadRoutes(selectedProject._id)
    }
  }, [selectedProject])

  async function checkAuth() {
    setAuthLoading(true)
    try {
      const res = await fetchCurrentUser()
      setUser(res.user)
    } catch {
      setUser(null)
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleAuthSubmit(e: FormEvent) {
    e.preventDefault()
    setAuthError('')
    try {
      if (authMode === 'login') {
        const res = await loginUser(email, password)
        setUser(res.user)
      } else {
        const res = await registerUser(email, password, name)
        setUser(res.user)
      }
      setEmail('')
      setPassword('')
      setName('')
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : 'Authentication failed')
    }
  }

  async function handleLogout() {
    try {
      await logoutUser()
      setUser(null)
    } catch (err) {
      console.error('Logout error:', err)
    }
  }

  async function loadProjects() {
    setProjectsLoading(true)
    setProjectsError('')
    try {
      const res = await fetchProjects()
      setProjects(res.projects)
    } catch (err) {
      setProjectsError(err instanceof Error ? err.message : 'Failed to load projects')
    } finally {
      setProjectsLoading(false)
    }
  }

  async function loadRoutes(projectId: string) {
    setRoutesLoading(true)
    setRoutesError('')
    try {
      const res = await fetchProjectRoutes(projectId)
      setRoutes(res.routes)
      setProjects(curr => curr.map(p => p._id === projectId ? { ...p, routeCount: res.routes.length } : p))
    } catch (err) {
      setRoutesError(err instanceof Error ? err.message : 'Failed to load routes')
    } finally {
      setRoutesLoading(false)
    }
  }

  function openCreateProjectModal() {
    setEditingProject(null)
    setProjectTitle('')
    setProjectTargetName('')
    setProjectTargetId('')
    setProjectMass(1000)
    setProjectMassUnit('g')
    setProjectNotes('')
    setProjectFormError('')
    setProjectModalOpen(true)
  }

  function openEditProjectModal(p: Project) {
    setEditingProject(p)
    setProjectTitle(p.title)
    setProjectTargetName(p.targetCompoundName || '')
    setProjectTargetId(p.targetCompoundId || '')
    setProjectMass(p.targetMassG)
    setProjectMassUnit(p.targetMassUnit)
    setProjectNotes(p.notes || '')
    setProjectFormError('')
    setProjectModalOpen(true)
  }

  async function handleProjectSubmit(e: FormEvent) {
    e.preventDefault()
    setProjectFormError('')

    if (!projectTitle.trim()) {
      setProjectFormError('Project title is required')
      return
    }

    if (projectMass <= 0) {
      setProjectFormError('Target mass must be a positive number')
      return
    }

    try {
      if (editingProject) {
        const res = await updateProject(editingProject._id, {
          title: projectTitle.trim(),
          targetCompoundName: projectTargetName.trim() || null,
          targetCompoundId: projectTargetId.trim() || null,
          targetMassG: projectMass,
          targetMassUnit: projectMassUnit,
          notes: projectNotes,
        })
        setProjects((curr) =>
          curr.map((p) => (p._id === editingProject._id ? res.project : p))
        )
        if (selectedProject?._id === editingProject._id) {
          setSelectedProject(res.project)
        }
      } else {
        const res = await createProject({
          title: projectTitle.trim(),
          targetCompoundName: projectTargetName.trim() || null,
          targetCompoundId: projectTargetId.trim() || null,
          targetMassG: projectMass,
          targetMassUnit: projectMassUnit,
          notes: projectNotes,
        })
        setProjects((curr) => [res.project, ...curr])
        setSelectedProject(res.project)
      }
      setProjectModalOpen(false)
    } catch (err) {
      setProjectFormError(err instanceof Error ? err.message : 'Failed to save project')
    }
  }

  async function handleDeleteProject(projectId: string) {
    if (!window.confirm('Are you sure you want to delete this project? All associated routes will also be deleted.')) {
      return
    }
    try {
      await deleteProject(projectId)
      setProjects((curr) => curr.filter((p) => p._id !== projectId))
      if (selectedProject?._id === projectId) {
        setSelectedProject(null)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete project')
    }
  }

  function openCreateRouteEditor() {
    setEditingRoute(null)
    setEditorOrigin('manual')
    setEditorSteps([
      {
        position: 1,
        name: 'Coupling Step',
        reactants: [],
        inputLabels: ['Starting Material A', 'Starting Material B'],
        productLabel: selectedProject?.targetCompoundName || 'Intermediate 1',
        yieldPercent: null,
        durationHours: null,
        durationUnit: 'h',
        solvent: '',
        conditions: '',
        hazardNotes: '',
        evidenceRef: '',
        dataSource: 'manual_entry',
      },
    ])
    setEditorError('')
    setSaveSuccessMsg('')
    setRouteEditorOpen(true)
  }

  function openEditRouteEditor(r: SavedRoute) {
    setEditingRoute(r)
    setEditorOrigin(r.origin)
    setEditorSteps(JSON.parse(JSON.stringify(r.steps)))
    setEditorError('')
    setSaveSuccessMsg('')
    setRouteEditorOpen(true)
  }

  function addStepToEditor() {
    setEditorSteps((curr) => [
      ...curr,
      {
        position: curr.length + 1,
        name: `Step ${curr.length + 1}`,
        reactants: [],
        inputLabels: [],
        productLabel: '',
        yieldPercent: null,
        durationHours: null,
        durationUnit: 'h',
        solvent: '',
        conditions: '',
        hazardNotes: '',
        evidenceRef: '',
        dataSource: 'manual_entry',
      },
    ])
  }

  function removeStepFromEditor(index: number) {
    setEditorSteps((curr) => {
      const filtered = curr.filter((_, i) => i !== index)
      return filtered.map((step, idx) => ({ ...step, position: idx + 1 }))
    })
  }

  function moveStep(index: number, direction: 'up' | 'down') {
    if (direction === 'up' && index === 0) return
    if (direction === 'down' && index === editorSteps.length - 1) return

    setEditorSteps((curr) => {
      const targetIndex = direction === 'up' ? index - 1 : index + 1
      const copy = [...curr]
      const [moved] = copy.splice(index, 1)
      copy.splice(targetIndex, 0, moved)
      return copy.map((step, idx) => ({ ...step, position: idx + 1 }))
    })
  }

  function updateStepField<K extends keyof SavedStep>(
    index: number,
    field: K,
    value: SavedStep[K]
  ) {
    setEditorSteps((curr) => {
      const copy = [...curr]
      copy[index] = { ...copy[index], [field]: value }
      return copy
    })
  }

  async function handleRouteSave() {
    if (!selectedProject) return
    setEditorError('')
    setSaveSuccessMsg('')

    // Validate steps
    for (const step of editorSteps) {
      if (step.yieldPercent !== null && (step.yieldPercent < 0 || step.yieldPercent > 100)) {
        setEditorError(`Step ${step.position} has an invalid yield (must be 0-100%).`)
        return
      }
      if (step.durationHours !== null && step.durationHours < 0) {
        setEditorError(`Step ${step.position} cannot have a negative duration.`)
        return
      }
    }

    try {
      if (editingRoute) {
        const res = await updateProjectRoute(selectedProject._id, editingRoute._id, {
          origin: editorOrigin,
          steps: editorSteps,
        })
        setRoutes((curr) =>
          curr.map((r) => (r._id === editingRoute._id ? res.route : r))
        )
        setEditingRoute(res.route)
        setSaveSuccessMsg(`Route draft saved successfully as Revision #${res.route.revision}!`)
      } else {
        const res = await createProjectRoute(selectedProject._id, {
          origin: editorOrigin,
          steps: editorSteps,
        })
        setRoutes((curr) => [res.route, ...curr])
        setEditingRoute(res.route)
        setSaveSuccessMsg(`Route draft created successfully (Revision #1)!`)
      }
      await loadProjects()
    } catch (err) {
      setEditorError(err instanceof Error ? err.message : 'Failed to save route')
    }
  }

  async function handleDeleteRoute(routeId: string) {
    if (!selectedProject || !window.confirm('Delete this route draft?')) return
    try {
      await deleteProjectRoute(selectedProject._id, routeId)
      setRoutes((curr) => curr.filter((r) => r._id !== routeId))
      if (editingRoute?._id === routeId) {
        setRouteEditorOpen(false)
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete route')
    }
  }

  if (authLoading) {
    return (
      <div className="workspace-panel loading-container">
        <div className="orbit loading"><span>⌁</span></div>
        <p>Loading user authentication session…</p>
      </div>
    )
  }

  // If not logged in, show Auth Gate
  if (!user) {
    return (
      <section className="workspace-panel auth-card" aria-label="Workspace Login">
        <div className="auth-header">
          <span className="brand-mark">S</span>
          <h2>SynthAI Workspace Account</h2>
          <p>Sign in or register to manage synthesis projects, manual route drafts, and comparisons.</p>
        </div>

        <div className="auth-tabs">
          <button
            className={authMode === 'login' ? 'active' : ''}
            onClick={() => { setAuthMode('login'); setAuthError('') }}
          >
            Sign In
          </button>
          <button
            className={authMode === 'register' ? 'active' : ''}
            onClick={() => { setAuthMode('register'); setAuthError('') }}
          >
            Create Account
          </button>
        </div>

        {authError && <div className="alert error"><b>Auth Error:</b> {authError}</div>}

        <form onSubmit={handleAuthSubmit} className="auth-form">
          {authMode === 'register' && (
            <label>
              Full Name
              <input
                type="text"
                placeholder="e.g. Marie Curie"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          )}

          <label>
            Email Address
            <input
              type="email"
              required
              placeholder="student@university.edu"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>

          <label>
            Password
            <input
              type="password"
              required
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          <button type="submit" className="primary">
            {authMode === 'login' ? 'Sign In to Workspace' : 'Register Account'}
          </button>
        </form>

        <div className="auth-demo-hint">
          <span>💡 Quick Student Demo:</span> You can register any valid email/password to create an isolated sandbox account.
        </div>
      </section>
    )
  }

  // Authenticated Workspace UI
  return (
    <div className="workspace-container">
      {/* Top User Bar */}
      <div className="workspace-user-bar">
        <div className="user-info">
          <span className="user-avatar">{user.name ? user.name[0].toUpperCase() : user.email[0].toUpperCase()}</span>
          <div>
            <strong>{user.name || user.email}</strong>
            <small>{user.email} · <span className="role-tag">{user.role}</span></small>
          </div>
        </div>
        <div className="user-actions">
          <button className="secondary" onClick={openCreateProjectModal}>+ New Project</button>
          <button className="logout-btn" onClick={handleLogout}>Sign Out</button>
        </div>
      </div>

      <div className="workspace-split">
        {/* Left Side: Projects Sidebar */}
        <aside className="workspace-sidebar">
          <div className="sidebar-header">
            <h3>My Projects ({projects.length})</h3>
            <button className="small-action" onClick={loadProjects} title="Refresh projects">↻</button>
          </div>

          {projectsLoading && (
            <div className="loading-state-small">
              <span className="spinner" /> Loading projects…
            </div>
          )}

          {projectsError && (
            <div className="alert error small-alert">
              <span>{projectsError}</span>
            </div>
          )}

          {!projectsLoading && projects.length === 0 && (
            <div className="empty-projects">
              <p>No projects yet.</p>
              <button className="primary small-btn" onClick={openCreateProjectModal}>Create First Project</button>
            </div>
          )}

          <div className="project-list-items">
            {projects.map((p) => (
              <div
                key={p._id}
                className={`project-list-item ${selectedProject?._id === p._id ? 'selected' : ''}`}
                onClick={() => { setSelectedProject(p); setRouteEditorOpen(false) }}
              >
                <div className="project-item-head">
                  <h4>{p.title}</h4>
                  <span className="mass-tag">{p.targetMassG} {p.targetMassUnit}</span>
                </div>
                <div className="project-item-meta">
                  <span>Target: {p.targetCompoundName || p.targetCompoundId || 'None'}</span>
                  <span className="route-count-tag">{p.routeCount || 0} routes</span>
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* Right Side: Selected Project Detail & Route Workspace */}
        <main className="workspace-main">
          {selectedProject ? (
            <div className="project-detail-view">
              <div className="project-detail-header">
                <div>
                  <span className="kicker">Project Workspace</span>
                  <h2>{selectedProject.title}</h2>
                  <p className="project-notes">{selectedProject.notes || 'No notes provided.'}</p>
                </div>
                <div className="project-detail-actions">
                  <button className="secondary" onClick={() => openEditProjectModal(selectedProject)}>Edit Project</button>
                  <button className="delete-btn" onClick={() => handleDeleteProject(selectedProject._id)}>Delete Project</button>
                </div>
              </div>

              <div className="project-stats-bar">
                <div><span>Target Compound</span><strong>{selectedProject.targetCompoundName || selectedProject.targetCompoundId || 'Unspecified'}</strong></div>
                <div><span>Target Batch Mass</span><strong>{selectedProject.targetMassG.toLocaleString()} {selectedProject.targetMassUnit}</strong></div>
                <div><span>Saved Routes</span><strong>{routes.length}</strong></div>
                <div><span>Created</span><strong>{new Date(selectedProject.createdAt).toLocaleDateString()}</strong></div>
              </div>

              {/* Workspace Navigation Tabs */}
              <div className="workspace-nav-tabs">
                <button
                  className={`ws-tab-btn ${workspaceTab === 'routes' ? 'active' : ''}`}
                  onClick={() => setWorkspaceTab('routes')}
                >
                  📝 Route Drafts & Revisions ({routes.length})
                </button>
                <button
                  className={`ws-tab-btn ${workspaceTab === 'compare' ? 'active' : ''}`}
                  onClick={() => setWorkspaceTab('compare')}
                >
                  ⚖️ Compare Routes & Snapshots
                </button>
              </div>

              {/* Routes List Section */}
              {workspaceTab === 'routes' && (
                <div className="project-routes-section">
                  <div className="routes-section-head">
                    <div>
                      <h3>Saved Route Revisions & Drafts</h3>
                      <p>Build manual routes, sequence reaction steps, and prepare for candidate comparison.</p>
                    </div>
                    <button className="primary" onClick={openCreateRouteEditor}>+ Add Route Draft</button>
                  </div>

                  {routesLoading && (
                    <div className="loading-state-small"><span className="spinner" /> Loading project routes…</div>
                  )}

                  {routesError && (
                    <div className="alert error"><span>{routesError}</span></div>
                  )}

                  {!routesLoading && routes.length === 0 && (
                    <div className="empty-routes-box">
                      <h4>No routes created for this project</h4>
                      <p>Start by creating a manual route draft with ordered reaction steps.</p>
                      <button className="secondary" onClick={openCreateRouteEditor}>Draft Manual Route</button>
                    </div>
                  )}

                  <div className="saved-routes-grid">
                    {routes.map((r) => (
                      <article key={r._id} className="saved-route-card">
                        <div className="saved-route-head">
                          <div>
                            <span className={`origin-badge origin-${r.origin}`}>{r.origin.toUpperCase()}</span>
                            <span className="revision-badge">Rev #{r.revision}</span>
                            <h4>Route ID: {r._id.slice(-6)}</h4>
                          </div>
                          <div className="saved-route-actions">
                            <button className="small-action" onClick={() => openEditRouteEditor(r)}>Edit Steps</button>
                            <button className="small-action delete-text" onClick={() => handleDeleteRoute(r._id)}>✕</button>
                          </div>
                        </div>

                        <div className="saved-route-steps-summary">
                          <strong>{r.steps.length} Steps:</strong>
                          <ol>
                            {r.steps.map((s) => (
                              <li key={s.position}>
                                <span>{s.name || `Step ${s.position}`}</span>
                                <small>
                                  {s.yieldPercent !== null ? `${s.yieldPercent}% yield` : 'Yield unentered'} ·{' '}
                                  {s.durationHours !== null ? `${s.durationHours} ${s.durationUnit}` : 'Duration unentered'}
                                </small>
                              </li>
                            ))}
                          </ol>
                        </div>

                        <div className="saved-route-foot">
                          <small>Updated: {new Date(r.updatedAt).toLocaleString()}</small>
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {/* Comparison Section */}
              {workspaceTab === 'compare' && (
                <RouteComparison
                  key={selectedProject._id}
                  project={selectedProject}
                  routes={routes}
                  onOpenCreateRoute={openCreateRouteEditor}
                />
              )}
            </div>
          ) : (
            <div className="no-project-selected">
              <div className="orbit"><span>📁</span></div>
              <h3>Select a Project or Create a New One</h3>
              <p>Choose an existing synthesis workspace from the left or create a new target project to start managing routes.</p>
              <button className="primary" onClick={openCreateProjectModal}>Create New Project</button>
            </div>
          )}
        </main>
      </div>

      {/* Create / Edit Project Modal */}
      {projectModalOpen && (
        <div className="modal-backdrop">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3>{editingProject ? 'Edit Project' : 'Create New Synthesis Project'}</h3>
              <button className="close-btn" onClick={() => setProjectModalOpen(false)}>✕</button>
            </div>

            {projectFormError && (
              <div className="alert error"><span>{projectFormError}</span></div>
            )}

            <form onSubmit={handleProjectSubmit}>
              <label>
                Project Title *
                <input
                  type="text"
                  required
                  placeholder="e.g. Demo Benzamide Scale-up Study"
                  value={projectTitle}
                  onChange={(e) => setProjectTitle(e.target.value)}
                />
              </label>

              <div className="field-row">
                <label>
                  Target Compound Name
                  <input
                    type="text"
                    placeholder="e.g. Demo benzamide target"
                    value={projectTargetName}
                    onChange={(e) => setProjectTargetName(e.target.value)}
                  />
                </label>
                <label>
                  Target Compound ID
                  <input
                    type="text"
                    placeholder="e.g. DEMO-TARGET-1"
                    value={projectTargetId}
                    onChange={(e) => setProjectTargetId(e.target.value)}
                  />
                </label>
              </div>

              <div className="benchmark-chips">
                <small>Quick autofill benchmark:</small>
                {benchmarkSuggestions.map((b) => (
                  <button
                    type="button"
                    key={b.id}
                    className="chip-btn"
                    onClick={() => {
                      setProjectTargetName(b.name)
                      setProjectTargetId(b.id)
                      if (!projectTitle) setProjectTitle(`Synthesis of ${b.name}`)
                    }}
                  >
                    {b.name}
                  </button>
                ))}
              </div>

              <div className="field-row">
                <label>
                  Target Batch Mass *
                  <input
                    type="number"
                    min="0.0001"
                    step="any"
                    required
                    value={projectMass}
                    onChange={(e) => setProjectMass(Number(e.target.value))}
                  />
                </label>
                <label>
                  Mass Unit
                  <select
                    value={projectMassUnit}
                    onChange={(e) => setProjectMassUnit(e.target.value as MassUnit)}
                  >
                    <option value="g">Grams (g)</option>
                    <option value="mg">Milligrams (mg)</option>
                    <option value="kg">Kilograms (kg)</option>
                  </select>
                </label>
              </div>

              <label>
                Project Notes / Objectives
                <textarea
                  rows={3}
                  placeholder="Notes on constraints, purity targets, hazardous reactant limits…"
                  value={projectNotes}
                  onChange={(e) => setProjectNotes(e.target.value)}
                />
              </label>

              <div className="modal-actions">
                <button type="button" className="secondary" onClick={() => setProjectModalOpen(false)}>Cancel</button>
                <button type="submit" className="primary">{editingProject ? 'Save Changes' : 'Create Project'}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Route Editor Modal */}
      {routeEditorOpen && (
        <div className="modal-backdrop">
          <div className="modal-dialog modal-large">
            <div className="modal-header">
              <div>
                <h3>{editingRoute ? `Edit Route (${editingRoute.origin.toUpperCase()} · Rev #${editingRoute.revision})` : 'New Route Draft'}</h3>
                <small>Sequence ordered steps. Missing yield or durations are safely preserved as incomplete draft states.</small>
              </div>
              <button className="close-btn" onClick={() => setRouteEditorOpen(false)}>✕</button>
            </div>

            {saveSuccessMsg && (
              <div className="alert success"><b>Success:</b> {saveSuccessMsg}</div>
            )}

            {editorError && (
              <div className="alert error"><b>Validation Error:</b> {editorError}</div>
            )}

            <div className="route-editor-body">
              <div className="editor-controls-bar">
                <label className="inline-label">
                  Origin:
                  <select
                    value={editorOrigin}
                    onChange={(e) => setEditorOrigin(e.target.value as 'manual' | 'generated')}
                  >
                    <option value="manual">Manual Entry</option>
                  </select>
                </label>

                <button type="button" className="secondary small-btn" onClick={addStepToEditor}>+ Add Step</button>
              </div>

              <div className="steps-editor-list">
                {editorSteps.map((step, index) => (
                  <div key={index} className="step-editor-card">
                    <div className="step-editor-head">
                      <div className="step-index-badge">Step #{step.position}</div>
                      <div className="step-reorder-btns">
                        <button
                          type="button"
                          className="step-btn"
                          disabled={index === 0}
                          onClick={() => moveStep(index, 'up')}
                          title="Move step up"
                        >
                          ▲
                        </button>
                        <button
                          type="button"
                          className="step-btn"
                          disabled={index === editorSteps.length - 1}
                          onClick={() => moveStep(index, 'down')}
                          title="Move step down"
                        >
                          ▼
                        </button>
                        <button
                          type="button"
                          className="step-btn delete-text"
                          onClick={() => removeStepFromEditor(index)}
                          title="Remove step"
                        >
                          ✕
                        </button>
                      </div>
                    </div>

                    <div className="step-editor-fields">
                      <label>
                        Reactants / Input Labels (one per line)
                        <textarea rows={2} value={step.inputLabels.join('\n')} onChange={e => updateStepField(index, 'inputLabels', e.target.value.split('\n'))} />
                      </label>
                      <div className="field-row">
                        <label>
                          Step Name
                          <input
                            type="text"
                            placeholder="e.g. Amidation reaction"
                            value={step.name}
                            onChange={(e) => updateStepField(index, 'name', e.target.value)}
                          />
                        </label>
                        <label>
                          Product Formed
                          <input
                            type="text"
                            placeholder="e.g. Crude Intermediate"
                            value={step.productLabel}
                            onChange={(e) => updateStepField(index, 'productLabel', e.target.value)}
                          />
                        </label>
                      </div>

                      <div className="field-row">
                        <label>
                          Yield Percentage (%)
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="any"
                            placeholder="0 - 100 (optional)"
                            value={step.yieldPercent ?? ''}
                            onChange={(e) =>
                              updateStepField(
                                index,
                                'yieldPercent',
                                e.target.value === '' ? null : Number(e.target.value)
                              )
                            }
                          />
                        </label>
                        <div className="field-row-compact">
                          <label>
                            Duration
                            <input
                              type="number"
                              min="0"
                              step="any"
                              placeholder=">= 0 (optional)"
                              value={step.durationHours ?? ''}
                              onChange={(e) =>
                                updateStepField(
                                  index,
                                  'durationHours',
                                  e.target.value === '' ? null : Number(e.target.value)
                                )
                              }
                            />
                          </label>
                          <label>
                            Unit
                            <select
                              value={step.durationUnit}
                              onChange={(e) =>
                                updateStepField(index, 'durationUnit', e.target.value as DurationUnit)
                              }
                            >
                              <option value="h">Hours (h)</option>
                              <option value="min">Minutes (min)</option>
                              <option value="d">Days (d)</option>
                            </select>
                          </label>
                        </div>
                      </div>

                      <div className="field-row">
                        <label>
                          Solvent
                          <input
                            type="text"
                            placeholder="e.g. DCM, EtOAc, Water"
                            value={step.solvent}
                            onChange={(e) => updateStepField(index, 'solvent', e.target.value)}
                          />
                        </label>
                        <label>
                          Reaction Conditions
                          <input
                            type="text"
                            placeholder="e.g. Reflux, 80°C, 1 atm"
                            value={step.conditions}
                            onChange={(e) => updateStepField(index, 'conditions', e.target.value)}
                          />
                        </label>
                      </div>

                      <div className="field-row">
                        <label>
                          Hazard Notes
                          <input
                            type="text"
                            placeholder="e.g. Strong exotherm, toxic gas evolution"
                            value={step.hazardNotes}
                            onChange={(e) => updateStepField(index, 'hazardNotes', e.target.value)}
                          />
                        </label>
                        <label>
                          Evidence Citation / Patent Ref
                          <input
                            type="text"
                            placeholder="e.g. US-2024-001234, Example 2"
                            value={step.evidenceRef}
                            onChange={(e) => updateStepField(index, 'evidenceRef', e.target.value)}
                          />
                        </label>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="modal-actions">
              <button type="button" className="secondary" onClick={() => setRouteEditorOpen(false)}>Close</button>
              <button type="button" className="primary" onClick={handleRouteSave}>Save Route Draft</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
