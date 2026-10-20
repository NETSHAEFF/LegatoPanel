const fs = require('fs');
const path = require('path');

// Auto-detect Python interpreter (virtual environment preferred)
const venvPython = path.join(__dirname, 'venv', 'bin', 'python3');
const pythonInterpreter = fs.existsSync(venvPython) ? venvPython : 'python3';

// Ensure logs directory exists
const logsDir = path.join(__dirname, 'logs');
if (!fs.existsSync(logsDir)) {
  try {
    fs.mkdirSync(logsDir, { recursive: true });
  } catch (e) {
    // Ignore error if directory creation fails
  }
}

module.exports = {
  apps: [
    {
      name: 'legato-panel',
      script: 'run_panel.py',
      cwd: __dirname,
      interpreter: pythonInterpreter,
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      restart_delay: 3000,
      kill_timeout: 5000,
      error_file: path.join(__dirname, 'logs', 'pm2-error.log'),
      out_file: path.join(__dirname, 'logs', 'pm2-out.log'),
      merge_logs: true,
      time: true,
      env: {
        HOST: '0.0.0.0',
        PORT: 80,
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
