import { mount } from 'svelte';
import 'leaflet/dist/leaflet.css';
import 'uplot/dist/uPlot.min.css';
import './app.css';
import App from './App.svelte';

mount(App, { target: document.getElementById('app') });
