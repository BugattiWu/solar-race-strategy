"""Display conversion only; model and telemetry remain SI/km/h."""
class Units:
    def __init__(self, imperial=False):
        self.imperial=imperial
        self.distance_factor=1/1.609344 if imperial else 1
        self.elevation_factor=3.280839895 if imperial else 1
        self.mass_factor=2.204622622 if imperial else 1
        self.distance='mi' if imperial else 'km'
        self.speed='mph' if imperial else 'km/h'
        self.elevation='ft' if imperial else 'm'
        self.mass='lb' if imperial else 'kg'
    def d(self,value): return value*self.distance_factor
    def km(self,value): return value/self.distance_factor
