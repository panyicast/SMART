(function () {
  const overlay = document.getElementById("overlay");
  const containerId = "viewer";
  const containerNode = document.getElementById(containerId);
  let bridge = null;
  let viewer = null;
  let resizeObserver = null;

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

    viewer = new Cesium.Viewer(containerId, options);
    viewer.clock.shouldAnimate = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#edf3f6");
    viewer.scene.globe.show = true;
    viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#7ea7bb");
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
          color: isShip ? Cesium.Color.fromCssColorString("#c25c38") : Cesium.Color.fromCssColorString("#0f7b8c"),
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 1.4,
        },
        label: {
          text: asset.name,
          font: "13px Segoe UI",
          fillColor: Cesium.Color.fromCssColorString("#10263b"),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString("rgba(255, 250, 242, 0.88)"),
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
          color: Cesium.Color.fromCssColorString("#d4aa3d"),
          outlineColor: Cesium.Color.fromCssColorString("#4d3b12"),
          outlineWidth: 1.4,
        },
        label: {
          text: relay.name,
          font: "13px Segoe UI",
          fillColor: Cesium.Color.fromCssColorString("#3e2f0f"),
          showBackground: true,
          backgroundColor: Cesium.Color.fromCssColorString("rgba(255, 244, 220, 0.88)"),
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
          material: Cesium.Color.fromCssColorString("#0f7b8c"),
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
        fillColor: Cesium.Color.fromCssColorString("#10263b"),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("rgba(255, 250, 242, 0.9)"),
        pixelOffset: new Cesium.Cartesian2(16, -18),
      },
    };

    entityOptions.point = {
      pixelSize: 12,
      color: Cesium.Color.fromCssColorString("#c25c38"),
      outlineColor: Cesium.Color.WHITE,
      outlineWidth: 1.4,
    };

    return viewer.entities.add(entityOptions);
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
      const cartographic = Cesium.Cartographic.fromCartesian(currentPosition);
      const fallbackRange = Number(sceneState.cameraRangeM) || 22000000.0;
      const fallbackHeight = Math.max(
        fallbackRange,
        (cartographic ? cartographic.height : 0.0) + 16000000.0
      );
      const fallbackDestination = Cesium.Cartesian3.fromRadians(
        cartographic ? cartographic.longitude : 0.0,
        cartographic ? cartographic.latitude + Cesium.Math.toRadians(6.0) : Cesium.Math.toRadians(18.0),
        fallbackHeight
      );

      viewer.camera.setView({
        destination: fallbackDestination,
        orientation: {
          heading: 0.0,
          pitch: -Cesium.Math.toRadians(85.0),
          roll: 0.0,
        },
      });
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
