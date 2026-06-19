"use strict";

const clock = document.querySelector("#heroClock");
const renderClock = () => {
  clock.textContent = new Intl.DateTimeFormat("tr-TR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
};
renderClock();
setInterval(renderClock, 1000);

const platform = /Android/i.test(navigator.userAgent)
  ? "android"
  : /Mac|iPhone|iPad/i.test(navigator.userAgent)
    ? "macos"
    : "windows";
document.querySelector(`[data-platform="${platform}"]`)?.classList.add("recommended");

const canvas = document.querySelector("#field");
const context = canvas.getContext("2d");
let points = [];

function resizeField() {
  const ratio = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(innerWidth * ratio);
  canvas.height = Math.round(innerHeight * ratio);
  canvas.style.width = `${innerWidth}px`;
  canvas.style.height = `${innerHeight}px`;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  points = Array.from({ length: Math.min(42, Math.round(innerWidth / 28)) }, () => ({
    x: Math.random() * innerWidth,
    y: Math.random() * innerHeight,
    speed: .08 + Math.random() * .18,
    size: .5 + Math.random() * 1.2,
  }));
}

function animateField() {
  context.clearRect(0, 0, innerWidth, innerHeight);
  context.fillStyle = "rgba(0, 233, 208, .42)";
  for (const point of points) {
    point.y -= point.speed;
    if (point.y < -4) point.y = innerHeight + 4;
    context.beginPath();
    context.arc(point.x, point.y, point.size, 0, Math.PI * 2);
    context.fill();
  }
  requestAnimationFrame(animateField);
}

addEventListener("resize", resizeField);
resizeField();
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) animateField();
