import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installDialogHooks } from './services/nativeDialog';
import { installAuthenticatedApiFetch } from './services/DataService';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('Failed to find the root element');

installDialogHooks();
installAuthenticatedApiFetch();

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
