"use strict";

const grid = document.querySelector("#network-grid");
const errorMessage = document.querySelector("#launcher-error");

function destinationFor(port) {
  const destination = new URL(window.location.href);
  destination.protocol = window.location.protocol;
  destination.hostname = window.location.hostname;
  destination.port = String(port);
  destination.pathname = "/";
  destination.search = "";
  destination.hash = "";
  return destination.href;
}

function createNetworkCard(network) {
  const link = document.createElement("a");
  link.className = `network-card accent-${network.accent || "cyan"}`;
  link.href = destinationFor(network.port);
  link.setAttribute("aria-label", `Open ${network.name}`);

  const top = document.createElement("div");
  top.className = "card-top";

  const eyebrow = document.createElement("span");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = network.eyebrow;

  const arrow = document.createElement("span");
  arrow.className = "arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "↗";

  const title = document.createElement("h3");
  title.textContent = network.name;

  const description = document.createElement("p");
  description.textContent = network.description;

  const action = document.createElement("span");
  action.className = "action";
  action.textContent = "Launch visualization";

  top.append(eyebrow, arrow);
  link.append(top, title, description, action);
  return link;
}

async function loadNetworks() {
  try {
    const response = await fetch("/assets/networks.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Configuration request failed: ${response.status}`);
    const config = await response.json();
    const visibleNetworks = config.networks.filter((network) => network.visible === true);
    visibleNetworks.forEach((network) => grid.append(createNetworkCard(network)));
    if (visibleNetworks.length === 0) throw new Error("No visible networks configured");
  } catch (error) {
    console.error("Unable to load launcher configuration", error);
    errorMessage.hidden = false;
  }
}

loadNetworks();
