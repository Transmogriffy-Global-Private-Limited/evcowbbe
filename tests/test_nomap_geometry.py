"""Source test cases for future pure-unit execution; NOT run on source installation."""
import pytest
from cow.domain.location_geometry import geodesic_m, classify_radius, GeometryError


def test_same_point_and_earth_surface():
    assert geodesic_m('22.6','88.4','22.6','88.4') == 0
    assert 111_000 <= geodesic_m('0','0','0','1') <= 112_000


def test_fence_with_accuracy_uncertainty():
    assert classify_radius('0','0','0','0',100,15).state == 'INSIDE'
    assert classify_radius('0','0','0','0',100,100).state == 'INSIDE'
    assert classify_radius('0','0','0','0',100,101).state == 'UNCERTAIN'
    assert classify_radius('0','0.002','0','0',100,20).state == 'OUTSIDE'
    assert classify_radius('0','0.0009','0','0',100,20).state == 'UNCERTAIN'


def test_coordinates_fail_closed():
    for bad in ('NaN','Infinity','91','-91',None,True):
        with pytest.raises(GeometryError):
            geodesic_m(bad,'0','0','0')
