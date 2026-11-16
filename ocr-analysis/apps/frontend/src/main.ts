import { mount } from 'svelte';
import './store/locale.svelte.ts'; // the chosen language, before anything renders a text
import App from './App.svelte';
import './app.css';

mount(App, { target: document.getElementById('app')! });
