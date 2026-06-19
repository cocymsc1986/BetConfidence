import { useState } from 'react';
import Signals from './tabs/Signals.jsx';
import Fixtures from './tabs/Fixtures.jsx';
import Player from './tabs/Player.jsx';
import Admin from './tabs/Admin.jsx';

const TABS = [
  { id: 'signals', label: 'Signals' },
  { id: 'fixtures', label: 'Fixtures' },
  { id: 'player', label: 'Player' },
  { id: 'admin', label: 'Admin' },
];

export default function App() {
  const [active, setActive] = useState('signals');

  return (
    <div className="app">
      <header>
        <h1>Pitch Edge</h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab-btn ${active === t.id ? 'active' : ''}`}
              onClick={() => setActive(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {active === 'signals' && <Signals />}
      {active === 'fixtures' && <Fixtures />}
      {active === 'player' && <Player />}
      {active === 'admin' && <Admin />}
    </div>
  );
}
