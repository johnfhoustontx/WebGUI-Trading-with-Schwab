def test_new_ports_exposed():
    import repo_paths as rp
    assert rp.MEMURAI_PORT == 6379
    assert rp.MEMURAI_URL == "redis://127.0.0.1:6379/0"
    assert rp.SERVICE_PORTS["sentiment"] == 8210
    assert rp.SERVICE_PORTS["options"] == 8211
    assert rp.SERVICE_PORTS["portfolio"] == 8212
    assert rp.SERVICE_PORTS["trade"] == 8213
    assert rp.SERVICE_PORTS["driver"] == 8214
    assert rp.SERVICE_URLS["sentiment"] == "http://127.0.0.1:8210"
