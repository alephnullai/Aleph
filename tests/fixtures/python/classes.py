"""Class hierarchy examples for Aleph testing."""


class Animal:
    def __init__(self, name, species):
        self.name = name
        self.species = species

    def speak(self):
        raise NotImplementedError("Subclasses must implement speak()")

    def describe(self):
        return f"{self.name} is a {self.species}"


class Dog(Animal):
    def __init__(self, name, breed):
        super().__init__(name, "dog")
        self.breed = breed

    def speak(self):
        return "Woof!"

    def fetch(self, item):
        return f"{self.name} fetches {item}"


class Cat(Animal):
    def __init__(self, name, indoor=True):
        super().__init__(name, "cat")
        self.indoor = indoor

    def speak(self):
        return "Meow!"

    def purr(self):
        return f"{self.name} is purring"


class Shelter:
    def __init__(self, name):
        self.name = name
        self._animals = []

    def add_animal(self, animal):
        self._animals.append(animal)

    def get_animal(self, name):
        for animal in self._animals:
            if animal.name == name:
                return animal
        return None

    def count(self):
        return len(self._animals)

    def list_species(self):
        return list(set(a.species for a in self._animals))
