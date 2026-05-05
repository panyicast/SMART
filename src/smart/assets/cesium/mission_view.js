(function () {
  const overlay = document.getElementById("overlay");
  const containerId = "viewer";
  const containerNode = document.getElementById(containerId);
  let bridge = null;
  let viewer = null;
  let resizeObserver = null;
  let creditContainer = null;
  const palette = {
    background: "#050a12",
    globe: "#172638",
    orbit: "#12b8c8",
    spacecraft: "#ff6b4a",
    ground: "#3bd6a3",
    ship: "#ff9b6a",
    relay: "#f2c94c",
    labelText: "#dff6ff",
    labelBackground: "rgba(6, 13, 24, 0.82)",
    labelOutline: "#0b1828",
    subsatellite: "#6fe7ff",
    earthVector: "#78a9ff",
    sunVector: "#ff9a5a",
    zVector: "#ffe45c",
  };

  function setOverlay(message, mode) {
    if (!overlay) {
      return;
    }
    if (!message) {
      overlay.style.display = "none";
      return;
    }
    overlay.style.display = "block";
    overlay.dataset.mode = mode || "loading";
    overlay.textContent = message;
  }

  function reportStatus(state, detail) {
    if (bridge && typeof bridge.reportStatus === "function") {
      bridge.reportStatus(state, detail || "");
    }
  }

  function normalizeOrDefault(vector, fallback) {
    const magnitude = Cesium.Cartesian3.magnitude(vector);
    if (!Number.isFinite(magnitude) || magnitude < 1.0e-6) {
      return Cesium.Cartesian3.clone(fallback, new Cesium.Cartesian3());
    }
    return Cesium.Cartesian3.divideByScalar(vector, magnitude, new Cesium.Cartesian3());
  }

  function cameraUpForDirection(direction) {
    let right = Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.UNIT_Z, new Cesium.Cartesian3());
    if (Cesium.Cartesian3.magnitude(right) < 1.0e-6) {
      right = Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.UNIT_Y, right);
    }
    right = normalizeOrDefault(right, Cesium.Cartesian3.UNIT_X);
    return normalizeOrDefault(
      Cesium.Cartesian3.cross(right, direction, new Cesium.Cartesian3()),
      Cesium.Cartesian3.UNIT_Z
    );
  }

  function ensureCesium() {
    if (window.__CESIUM_LOAD_FAILED__ || typeof window.Cesium === "undefined") {
      setOverlay("CesiumJS failed to load from the packaged SMART assets.", "error");
      reportStatus("library_error", "");
      return false;
    }
    return true;
  }

  function attachResizeHandling() {
    if (!containerNode) {
      return;
    }

    const handleResize = () => {
      if (!viewer) {
        return;
      }
      viewer.resize();
      viewer.scene.requestRender();
    };

    window.addEventListener("resize", handleResize);
    if (resizeObserver || typeof ResizeObserver === "undefined") {
      return;
    }
    resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerNode);
  }

  function createViewer() {
    if (viewer) {
      return viewer;
    }

    const options = {
      animation: false,
      timeline: false,
      fullscreenButton: false,
      geocoder: false,
      homeButton: false,
      infoBox: false,
      navigationHelpButton: false,
      sceneModePicker: false,
      selectionIndicator: false,
      baseLayerPicker: false,
      baseLayer: false,
      shouldAnimate: false,
      scene3DOnly: true,
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    };
    creditContainer = document.createElement("div");
    creditContainer.style.display = "none";
    document.body.appendChild(creditContainer);
    options.creditContainer = creditContainer;

    viewer = new Cesium.Viewer(containerId, options);
    viewer.clock.shouldAnimate = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString(palette.background);
    viewer.scene.globe.show = true;
    viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString(palette.globe);
    viewer.scene.globe.enableLighting = false;
    viewer.scene.globe.showGroundAtmosphere = false;
    viewer.scene.globe.depthTestAgainstTerrain = false;
    viewer.scene.fog.enabled = false;
    viewer.scene.highDynamicRange = false;
    if (viewer.scene.skyAtmosphere) {
      viewer.scene.skyAtmosphere.show = false;
    }
    if (viewer.scene.skyBox) {
      viewer.scene.skyBox.show = false;
    }
    if (viewer.scene.moon) {
      viewer.scene.moon.show = false;
    }
    if (viewer.scene.renderError) {
      viewer.scene.renderError.addEventListener(function (_scene, error) {
        console.error(error);
        setOverlay(`Cesium scene render failed: ${error}`, "error");
        reportStatus("scene_error", String(error));
      });
    }
    attachResizeHandling();
    return viewer;
  }

  function toCartesianPoints(pointTriples) {
    return (pointTriples || []).map((point) => new Cesium.Cartesian3(point[0], point[1], point[2]));
  }

  function applyEarthTexture(sceneState) {
    if (!viewer) {
      return;
    }

    viewer.imageryLayers.removeAll();
    if (!sceneState.earthTextureUrl) {
      return;
    }

    try {
      const provider = new Cesium.SingleTileImageryProvider({
        url: sceneState.earthTextureUrl,
        rectangle: Cesium.Rectangle.fromDegrees(-180.0, -90.0, 180.0, 90.0),
      });
      viewer.imageryLayers.addImageryProvider(provider);
    } catch (error) {
      console.error(error);
    }
  }

  function addGroundAssets(sceneState) {
    (sceneState.groundAssets || []).forEach((asset) => {
      const isShip = String(asset.assetType || "").toLowerCase().includes("ship");
      viewer.entities.add({
        name: asset.name,
        position: Cesium.Cartesian3.fromDegrees(
          asset.longitudeDeg,
          asset.latitudeDeg,
          asset.altitudeM || 0.0
        ),
        point: {
          pixelSize: isShip ? 10 : 9,
          color: isShip ? Cesium.Color.fromCssColorString(palette.ship) : Cesium.Color.fromCssColorString(palette.ground),
          outlineColor: Cesium.Color.fromCssColorString(palette.labelOutline),
          outlineWidth: 1.4,
        },
        label: {
          text: asset.name,
          font: "13px Segoe UI",
          fillColor: Cesium.Color.fromCssColorString(palette.labelText),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString(palette.labelBackground),
          pixelOffset: new Cesium.Cartesian2(12, -14),
          style: Cesium.LabelStyle.FILL,
        },
      });
    });
  }

  function addRelaySatellites(sceneState) {
    (sceneState.relaySatellites || []).forEach((relay) => {
      viewer.entities.add({
        name: relay.name,
        position: Cesium.Cartesian3.fromDegrees(relay.longitudeDeg, 0.0, relay.altitudeM || 0.0),
        point: {
          pixelSize: 11,
          color: Cesium.Color.fromCssColorString(palette.relay),
          outlineColor: Cesium.Color.fromCssColorString(palette.labelOutline),
          outlineWidth: 1.4,
        },
        label: {
          text: relay.name,
          font: "13px Segoe UI",
          fillColor: Cesium.Color.fromCssColorString(palette.labelText),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString(palette.labelBackground),
          pixelOffset: new Cesium.Cartesian2(12, -14),
        },
      });
    });
  }

  function addOrbit(sceneState) {
    const orbitPositions = toCartesianPoints(sceneState.orbitPositionsM);
    if (orbitPositions.length >= 2) {
      viewer.entities.add({
        name: "Orbit",
        polyline: {
          positions: orbitPositions,
          width: 2.8,
          material: Cesium.Color.fromCssColorString(palette.orbit),
        },
      });
    }
    return orbitPositions;
  }

  function addSpacecraft(sceneState) {
    const currentPosition = new Cesium.Cartesian3(
      sceneState.currentPositionM[0],
      sceneState.currentPositionM[1],
      sceneState.currentPositionM[2]
    );

    const entityOptions = {
      name: sceneState.satelliteLabel || "Mission Spacecraft",
      position: currentPosition,
      label: {
        text: sceneState.satelliteLabel || "Mission Spacecraft",
        font: "13px Segoe UI",
        fillColor: Cesium.Color.fromCssColorString(palette.labelText),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString(palette.labelBackground),
        pixelOffset: new Cesium.Cartesian2(16, -18),
      },
    };

    entityOptions.point = {
      pixelSize: 12,
      color: Cesium.Color.fromCssColorString(palette.spacecraft),
      outlineColor: Cesium.Color.fromCssColorString("#fff3e9"),
      outlineWidth: 1.4,
    };

    return viewer.entities.add(entityOptions);
  }

  function addVectorOverlay(name, origin, direction, color, lengthM) {
    if (!origin || !direction) {
      return;
    }
    const scale = Number(lengthM) || 2500000.0;
    const start = new Cesium.Cartesian3(origin[0], origin[1], origin[2]);
    const end = new Cesium.Cartesian3(
      origin[0] + direction[0] * scale,
      origin[1] + direction[1] * scale,
      origin[2] + direction[2] * scale
    );
    viewer.entities.add({
      name,
      polyline: {
        positions: [start, end],
        width: 4,
        material: Cesium.Color.fromCssColorString(color),
      },
      label: {
        text: name,
        font: "12px Segoe UI",
        fillColor: Cesium.Color.fromCssColorString(color),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("rgba(3, 8, 16, 0.78)"),
        pixelOffset: new Cesium.Cartesian2(10, -10),
      },
      position: end,
    });
  }

  function addFlightOverlays(sceneState, currentPosition) {
    const origin = sceneState.currentPositionM;
    const range = Math.max(1200000.0, (Number(sceneState.cameraRangeM) || 22000000.0) * 0.12);
    addVectorOverlay("+Z", origin, sceneState.attitudePlusZ, palette.zVector, range);
    addVectorOverlay("Sun", origin, sceneState.sunDirection, palette.sunVector, range * 1.15);
    addVectorOverlay("Earth", origin, sceneState.earthDirection, palette.earthVector, range * 0.85);

    if (sceneState.subsatellitePoint) {
      viewer.entities.add({
        name: "Subsatellite Point",
        position: Cesium.Cartesian3.fromDegrees(
          sceneState.subsatellitePoint.longitudeDeg,
          sceneState.subsatellitePoint.latitudeDeg,
          0.0
        ),
        point: {
          pixelSize: 8,
          color: Cesium.Color.fromCssColorString(palette.subsatellite),
          outlineColor: Cesium.Color.fromCssColorString(palette.labelOutline),
          outlineWidth: 1.4,
        },
        polyline: {
          positions: [currentPosition, Cesium.Cartesian3.fromDegrees(
            sceneState.subsatellitePoint.longitudeDeg,
            sceneState.subsatellitePoint.latitudeDeg,
            0.0
          )],
          width: 1.6,
          material: Cesium.Color.fromCssColorString("rgba(111, 231, 255, 0.55)"),
        },
      });
    }
  }

  function setEarthCenteredCamera(sceneState, currentPosition) {
    const fallbackRange = Number(sceneState.cameraRangeM) || 22000000.0;
    const range = Math.max(fallbackRange * 1.12, 26000000.0);
    viewer.camera.lookAt(
      Cesium.Cartesian3.ZERO,
      new Cesium.HeadingPitchRange(0.0, -Cesium.Math.toRadians(32.0), range)
    );
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
  }

  function setSpacecraftCenteredCamera(sceneState, currentPosition) {
    const fallbackRange = Number(sceneState.cameraRangeM) || 22000000.0;
    const target = currentPosition || new Cesium.Cartesian3(7000000.0, 0.0, 0.0);
    const range = Math.max(fallbackRange * 0.32, 3500000.0);
    const radial = normalizeOrDefault(target, Cesium.Cartesian3.UNIT_X);
    const destination = Cesium.Cartesian3.add(
      target,
      Cesium.Cartesian3.multiplyByScalar(radial, range, new Cesium.Cartesian3()),
      new Cesium.Cartesian3()
    );
    const direction = normalizeOrDefault(
      Cesium.Cartesian3.subtract(target, destination, new Cesium.Cartesian3()),
      Cesium.Cartesian3.negate(radial, new Cesium.Cartesian3())
    );
    viewer.camera.setView({
      destination,
      orientation: {
        direction,
        up: cameraUpForDirection(direction),
      },
    });
  }

  function setCamera(sceneState, currentPosition) {
    if (sceneState.cameraMode === "spacecraft") {
      setSpacecraftCenteredCamera(sceneState, currentPosition);
      return;
    }
    setEarthCenteredCamera(sceneState, currentPosition);
  }

  function renderScene(payloadText) {
    if (!ensureCesium()) {
      return;
    }

    try {
      const sceneState = JSON.parse(payloadText);
      createViewer();
      viewer.entities.removeAll();
      applyEarthTexture(sceneState);

      const spacecraft = addSpacecraft(sceneState);
      addOrbit(sceneState);
      addGroundAssets(sceneState);
      addRelaySatellites(sceneState);
      const currentPosition = spacecraft.position.getValue(Cesium.JulianDate.now());
      addFlightOverlays(sceneState, currentPosition);
      setCamera(sceneState, currentPosition);
      viewer.scene.requestRender();
      window.requestAnimationFrame(() => {
        if (!viewer) {
          return;
        }
        viewer.resize();
        viewer.scene.requestRender();
        setOverlay("");
        reportStatus("ready", "");
      });
    } catch (error) {
      console.error(error);
      setOverlay(`Cesium scene render failed: ${error}`, "error");
      reportStatus("scene_error", String(error));
    }
  }

  function bootstrapChannel() {
    if (typeof window.qt === "undefined" || !window.qt.webChannelTransport) {
      setOverlay("Qt WebChannel is unavailable.", "error");
      reportStatus("scene_error", "Qt WebChannel is unavailable.");
      return;
    }

    new QWebChannel(window.qt.webChannelTransport, (channel) => {
      bridge = channel.objects.bridge;
      if (!bridge || typeof bridge.scene_changed === "undefined") {
        setOverlay("SMART bridge is unavailable.", "error");
        reportStatus("scene_error", "SMART bridge is unavailable.");
        return;
      }

      bridge.scene_changed.connect(renderScene);
      setOverlay("Loading Cesium mission scene...", "loading");
      if (typeof bridge.requestScene === "function") {
        bridge.requestScene();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrapChannel, { once: true });
  } else {
    bootstrapChannel();
  }
})();
