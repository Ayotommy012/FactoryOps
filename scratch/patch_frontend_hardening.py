with open('frontend/src/components/Header.tsx', 'rb') as f:
    raw = f.read()

content = raw.replace(b'\r\r\n', b'\n').replace(b'\r\n', b'\n').decode('utf-8')

target_badge = """            <div>
              {isAwaitingDecision ? (
                <span className="badge badge-warning" style={{ fontSize: '0.70rem' }} title="Automatic progression paused awaiting operator recovery decision">
                  ⚠️ Awaiting Decision
                </span>
              ) : scenario.status === 'recovering' ? (
                <span className="badge badge-info" style={{ fontSize: '0.70rem' }} title="Maintenance recovery in progress">
                  🔧 Recovering ({scenario.parameters?.recovery?.remaining_steps ?? 0} steps left)
                </span>
              ) : scenario.parameters?.auto_advance === false ? (
                <span className="badge badge-neutral" style={{ fontSize: '0.70rem' }} title="Automatic progression paused by operator">
                  ⏸️ Auto Paused
                </span>
              ) : (
                <span className="badge badge-nominal" style={{ fontSize: '0.70rem' }} title="Automatic 5-second simulation progression active">
                  🟢 Auto (1 step / 5s)
                </span>
              )}
            </div>"""

replacement_badge = """            <div>
              {scenario.status === 'completed' ? (
                <span className="badge badge-nominal" style={{ fontSize: '0.70rem' }} title="Simulation completed all batches">
                  🏁 Completed
                </span>
              ) : scenario.status === 'failed' ? (
                <span className="badge badge-danger" style={{ fontSize: '0.70rem' }} title="Simulation halted due to failure">
                  🛑 Halted (Failed)
                </span>
              ) : scenario.status === 'cancelled' ? (
                <span className="badge badge-neutral" style={{ fontSize: '0.70rem' }} title="Simulation cancelled">
                  ⏹️ Cancelled
                </span>
              ) : isAwaitingDecision ? (
                <span className="badge badge-warning" style={{ fontSize: '0.70rem' }} title="Automatic progression paused awaiting operator recovery decision">
                  ⚠️ Awaiting Decision
                </span>
              ) : scenario.status === 'recovering' ? (
                <span className="badge badge-info" style={{ fontSize: '0.70rem' }} title="Maintenance recovery in progress">
                  🔧 Recovering ({scenario.parameters?.recovery?.remaining_steps ?? 0} steps left)
                </span>
              ) : scenario.parameters?.auto_advance === false ? (
                <span className="badge badge-neutral" style={{ fontSize: '0.70rem' }} title="Automatic progression paused by operator">
                  ⏸️ Auto Paused
                </span>
              ) : (
                <span className="badge badge-nominal" style={{ fontSize: '0.70rem' }} title="Automatic 5-second simulation progression active">
                  🟢 Auto (1 step / 5s)
                </span>
              )}
            </div>"""

assert target_badge in content, "Target badge not found in Header.tsx"
content = content.replace(target_badge, replacement_badge, 1)

target_btn = """          <button
            className="btn btn-primary"
            onClick={() => onStep(1)}
            disabled={loading || !scenario}
            title="Advance factory simulation clock by 1 discrete step (15 mins)"
          >
            <Play size={14} />
            Step +1
          </button>

          <button
            className="btn btn-warning"
            onClick={() => onStep(3)}
            disabled={loading || !scenario}
            title="Advance simulation by 3 steps directly to trigger roaster bearing thermal runaway"
          >"""

replacement_btn = """          <button
            className="btn btn-primary"
            onClick={() => onStep(1)}
            disabled={loading || !scenario || scenario.status === 'completed' || scenario.status === 'failed' || scenario.status === 'cancelled'}
            title="Advance factory simulation clock by 1 discrete step (15 mins)"
          >
            <Play size={14} />
            Step +1
          </button>

          <button
            className="btn btn-warning"
            onClick={() => onStep(3)}
            disabled={loading || !scenario || scenario.status === 'completed' || scenario.status === 'failed' || scenario.status === 'cancelled'}
            title="Advance simulation by 3 steps directly to trigger roaster bearing thermal runaway"
          >"""

assert target_btn in content, "Target btn not found in Header.tsx"
content = content.replace(target_btn, replacement_btn, 1)

with open('frontend/src/components/Header.tsx', 'wb') as f:
    f.write(content.replace('\n', '\r\n').encode('utf-8'))

print("Successfully updated Header.tsx")
